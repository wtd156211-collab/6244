"""渲染引擎：模板缓存、作用域链、输出状态机、分段记录。"""

import os
import re

from .errors import TemplateError
from .escape import escape_attr, escape_script, escape_text, escape_url, _URL_ATTRS
from .parser import Block, Extends, For, If, Include, Text, Var, parse

_TEXT_OPEN_RE = re.compile(r"<(?=[A-Za-z/])")
_TAG_SPECIAL_RE = re.compile(r'["=>]')
_NAME_CH = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_TOKEN_CH = _NAME_CH | frozenset(":_-.")

_MAX_INCLUDE_DEPTH = 16


class Template:
    __slots__ = ("path", "nodes", "parent", "overrides")

    def __init__(self, path, nodes, parent=None, overrides=None):
        self.path = path
        self.nodes = nodes
        self.parent = parent
        self.overrides = overrides  # {block 名: Block}


class Segment:
    """一段输出。kind: literal/value；context: text/tag/attr/url/script；
    escape: literal/escape_text/escape_attr/escape_url/escape_script/raw。"""

    __slots__ = ("kind", "context", "escape", "tpl", "path", "raw", "text",
                 "replaced", "note")

    def __init__(self, kind, context, escape, tpl, path, raw=None, text="",
                 replaced=None, note=None):
        self.kind = kind
        self.context = context
        self.escape = escape
        self.tpl = tpl
        self.path = path
        self.raw = raw
        self.text = text
        self.replaced = replaced if replaced is not None else []
        self.note = note


class _RenderState:
    __slots__ = ("out", "segments", "scopes", "include_stack", "state",
                 "tag_open", "capture", "tag_name", "token", "after_eq",
                 "attr_token", "attr_name", "script_pending",
                 "last_tpl", "last_line", "last_col")

    def __init__(self, collect):
        self.out = []
        self.segments = [] if collect else None
        self.scopes = []
        self.include_stack = []
        self.state = "text"
        self.tag_open = True
        self.capture = False
        self.tag_name = ""
        self.token = ""
        self.after_eq = False
        self.attr_token = ""
        self.attr_name = ""
        self.script_pending = ""
        self.last_tpl = None
        self.last_line = 1
        self.last_col = 1


def _advance(line, col, chunk):
    nl = chunk.count("\n")
    if nl:
        return line + nl, len(chunk) - chunk.rfind("\n")
    return line, col + len(chunk)


class Engine:
    """编译一次、渲染多次：解析结果按模板路径缓存。"""

    def __init__(self):
        self.cache = {}
        self.loading = set()

    # ---------- 模板加载 ----------

    def load(self, path):
        path = os.path.normpath(path)
        if path in self.cache:
            return self.cache[path]
        if path in self.loading:
            raise TemplateError("E_RECURSE", "模板继承成环：%s" % path)
        self.loading.add(path)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            raise TemplateError("E_TPL", "找不到模板 %s" % path)
        nodes = parse(src, path)
        template = self._build(path, nodes)
        self.cache[path] = template
        self.loading.discard(path)
        return template

    def _build(self, path, nodes):
        extends = None
        rest = []
        for node in nodes:
            if isinstance(node, Extends):
                extends = node
            else:
                rest.append(node)
        if extends is None:
            return Template(path, nodes)
        for node in rest:
            ok = isinstance(node, Block) or (
                isinstance(node, Text) and not node.text.strip())
            if not ok:
                line = getattr(node, "line", 1)
                col = getattr(node, "col", 1)
                raise TemplateError(
                    "E_SYNTAX",
                    "使用 extends 的模板里，extends/block/注释/空白之外"
                    "不能有别的内容", path, line, col)
        parent_path = os.path.normpath(
            os.path.join(os.path.dirname(path), extends.name))
        parent = self.load(parent_path)
        if parent.parent is not None:
            raise TemplateError("E_SYNTAX", "不支持多级继承",
                                path, extends.line, extends.col)
        parent_blocks = {}
        _collect_blocks(parent.nodes, parent_blocks)
        overrides = {}
        for node in rest:
            if isinstance(node, Block):
                if node.name not in parent_blocks:
                    raise TemplateError(
                        "E_NAME", "父模板里没有名为 %s 的 block" % node.name,
                        path, node.line, node.col)
                overrides[node.name] = node
        return Template(path, None, parent=parent, overrides=overrides)

    # ---------- 渲染 ----------

    def render(self, template_path, data, collect=False):
        """渲染入口模板，返回 (输出文本, 分段列表或 None)。"""
        template = self.load(template_path)
        rs = _RenderState(collect)
        rs.scopes.append(data)
        rs.include_stack.append(template.path)
        self._render_template(template, rs, None)
        self._finish(rs)
        return "".join(rs.out), rs.segments

    def _render_template(self, template, rs, overrides):
        if template.parent is not None:
            self._render_nodes(template.parent.nodes, template.parent.path,
                               rs, template.overrides)
        else:
            self._render_nodes(template.nodes, template.path, rs, overrides)

    def _render_nodes(self, nodes, tpl, rs, overrides):
        for node in nodes:
            kind = type(node)
            if kind is Text:
                self._feed_text(rs, node, tpl)
            elif kind is Var:
                self._render_var(rs, node, tpl)
            elif kind is If:
                self._render_if(rs, node, tpl, overrides)
            elif kind is For:
                self._render_for(rs, node, tpl, overrides)
            elif kind is Include:
                self._render_include(rs, node, tpl, overrides)
            elif kind is Block:
                if overrides is not None and node.name in overrides:
                    override = overrides[node.name]
                    self._render_nodes(override.body, override.tpl,
                                       rs, overrides)
                else:
                    self._render_nodes(node.body, tpl, rs, overrides)

    def _render_if(self, rs, node, tpl, overrides):
        for path, negate, body in node.branches:
            value = self._eval_path(rs, path, None, tpl, node)
            if negate:
                value = not value
            if value:
                self._render_nodes(body, tpl, rs, overrides)
                return
        self._render_nodes(node.else_body, tpl, rs, overrides)

    def _render_for(self, rs, node, tpl, overrides):
        value = self._eval_path(rs, node.path, node.path_str, tpl, node)
        if not isinstance(value, list):
            raise TemplateError(
                "E_TYPE", "%s 不是列表，不能循环" % node.path_str,
                tpl, node.line, node.col)
        total = len(value)
        for index, item in enumerate(value):
            rs.scopes.append({
                node.name: item,
                "loop": {"index": index + 1,
                         "first": index == 0,
                         "last": index == total - 1},
            })
            try:
                self._render_nodes(node.body, tpl, rs, overrides)
            finally:
                rs.scopes.pop()

    def _render_include(self, rs, node, tpl, overrides):
        inc_path = os.path.normpath(
            os.path.join(os.path.dirname(tpl), node.name))
        if inc_path in rs.include_stack:
            raise TemplateError("E_RECURSE", "模板展开成环：%s" % inc_path,
                                tpl, node.line, node.col)
        if len(rs.include_stack) > _MAX_INCLUDE_DEPTH:
            raise TemplateError("E_RECURSE", "模板展开超过 %d 层"
                                % _MAX_INCLUDE_DEPTH, tpl, node.line, node.col)
        if not os.path.isfile(inc_path):
            raise TemplateError("E_TPL", "找不到模板 %s" % inc_path,
                                tpl, node.line, node.col)
        rs.include_stack.append(inc_path)
        try:
            sub = self.load(inc_path)
            self._render_template(sub, rs, overrides)
        except TemplateError as err:
            err.via.append((tpl, node.line))
            raise
        finally:
            rs.include_stack.pop()

    # ---------- 求值 ----------

    def _eval_path(self, rs, path, path_str, tpl, node):
        first, segs = path
        shown = path_str if path_str is not None else first
        value = _MISSING
        for frame in reversed(rs.scopes):
            if first in frame:
                value = frame[first]
                break
        if value is _MISSING:
            raise TemplateError("E_DATA", "缺少变量 %s" % shown,
                                tpl, node.line, node.col)
        for kind, key in segs:
            if kind == "key":
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    raise TemplateError("E_DATA", "缺少变量 %s" % shown,
                                        tpl, node.line, node.col)
            else:
                if isinstance(value, list) and 0 <= key < len(value):
                    value = value[key]
                else:
                    raise TemplateError("E_DATA", "缺少变量 %s" % shown,
                                        tpl, node.line, node.col)
        return value

    def _render_var(self, rs, node, tpl):
        value = self._eval_path(rs, node.path, node.path_str, tpl, node)
        text = _to_string(value, node, tpl)
        state = rs.state
        if node.raw and state != "text":
            raise TemplateError("E_CTX", "| raw 只能用在标签之间",
                                tpl, node.line, node.col)
        note = None
        if state == "text":
            if node.raw:
                out, replaced, escape = text, [], "raw"
            else:
                out, replaced = escape_text(text)
                escape = "escape_text"
            context = "text"
        elif state == "attr":
            if rs.attr_name.lower() in _URL_ATTRS:
                out, replaced, note = escape_url(text)
                escape = "escape_url"
                context = "url"
            else:
                out, replaced = escape_attr(text)
                escape = "escape_attr"
                context = "attr"
        elif state == "script":
            out, replaced = escape_script(text)
            escape = "escape_script"
            context = "script"
        else:
            raise TemplateError(
                "E_CTX", "变量只能放在用双引号括起来的属性值里",
                tpl, node.line, node.col)
        rs.out.append(out)
        if rs.segments is not None:
            rs.segments.append(Segment("value", context, escape, tpl,
                                       node.path_str, raw=text, text=out,
                                       replaced=replaced, note=note))

    # ---------- 输出状态机 ----------

    def _literal(self, rs, chunk, context, tpl):
        if not chunk:
            return
        rs.out.append(chunk)
        segments = rs.segments
        if segments is not None:
            last = segments[-1] if segments else None
            if (last is not None and last.kind == "literal"
                    and last.context == context and last.tpl == tpl):
                last.text += chunk
            else:
                segments.append(Segment("literal", context, "literal", tpl,
                                        None, text=chunk))

    def _feed_text(self, rs, node, tpl):
        text = node.text
        if rs.script_pending:
            text = rs.script_pending + text
            rs.script_pending = ""
        i = 0
        n = len(text)
        while i < n:
            state = rs.state
            if state == "text":
                m = _TEXT_OPEN_RE.search(text, i)
                if m is None:
                    self._literal(rs, text[i:], "text", tpl)
                    break
                j = m.start()
                if j > i:
                    self._literal(rs, text[i:j], "text", tpl)
                self._literal(rs, "<", "tag", tpl)
                rs.state = "tag"
                rs.tag_open = text[j + 1] != "/"
                rs.capture = True
                rs.tag_name = ""
                rs.token = ""
                rs.after_eq = False
                i = j + 1
            elif state == "tag":
                m = _TAG_SPECIAL_RE.search(text, i)
                end = m.start() if m is not None else n
                if end > i:
                    self._tag_run(rs, text, i, end, tpl, node)
                    self._literal(rs, text[i:end], "tag", tpl)
                if m is None:
                    break
                ch = text[end]
                if ch == '"':
                    if rs.after_eq:
                        rs.attr_name = rs.attr_token
                        rs.state = "attr"
                        self._literal(rs, '"', "attr", tpl)
                    else:
                        self._literal(rs, '"', "tag", tpl)
                    rs.after_eq = False
                    rs.token = ""
                elif ch == "=":
                    rs.after_eq = True
                    rs.attr_token = rs.token
                    rs.token = ""
                    self._literal(rs, "=", "tag", tpl)
                else:
                    self._literal(rs, ">", "tag", tpl)
                    rs.after_eq = False
                    if rs.tag_open and rs.tag_name.lower() == "script":
                        rs.state = "script"
                    else:
                        rs.state = "text"
                i = end + 1
            elif state == "attr":
                j = text.find('"', i)
                if j < 0:
                    self._literal(rs, text[i:], "attr", tpl)
                    break
                if j > i:
                    self._literal(rs, text[i:j], "attr", tpl)
                self._literal(rs, '"', "attr", tpl)
                rs.state = "tag"
                rs.token = ""
                i = j + 1
            else:  # script
                j = text.find("<", i)
                if j < 0:
                    self._literal(rs, text[i:], "script", tpl)
                    break
                if j > i:
                    self._literal(rs, text[i:j], "script", tpl)
                tail = text[j:]
                low = tail[1:8].lower()
                if low == "/script" and len(tail) > 8 \
                        and tail[8] not in _NAME_CH:
                    self._literal(rs, tail[:8], "tag", tpl)
                    rs.state = "tag"
                    rs.tag_open = False
                    rs.capture = False
                    rs.tag_name = "script"
                    rs.token = ""
                    rs.after_eq = False
                    i = j + 8
                elif len(tail) <= 8 and "/script".startswith(tail[1:].lower()):
                    rs.script_pending = tail
                    break
                else:
                    self._literal(rs, "<", "script", tpl)
                    i = j + 1
        rs.last_tpl = tpl
        rs.last_line, rs.last_col = _advance(node.line, node.col, node.text)

    def _tag_run(self, rs, text, start, end, tpl, node):
        for k in range(start, end):
            ch = text[k]
            if rs.capture:
                if ch in _NAME_CH:
                    rs.tag_name += ch
                else:
                    rs.capture = False
            if rs.after_eq and not ch.isspace():
                line, col = _advance(node.line, node.col, text[:k])
                if ch == "'":
                    raise TemplateError(
                        "E_SYNTAX", "属性值不支持单引号，请使用双引号",
                        tpl, line, col)
                raise TemplateError(
                    "E_SYNTAX", "属性值要用双引号括起来", tpl, line, col)
            if ch.isspace() or ch == "/":
                rs.token = ""
                if ch.isspace():
                    rs.after_eq = False
            elif ch in _TOKEN_CH:
                rs.token += ch
            else:
                rs.token = ""

    def _finish(self, rs):
        if rs.script_pending:
            self._literal(rs, rs.script_pending, "script", rs.last_tpl)
            rs.script_pending = ""
        if rs.state != "text":
            raise TemplateError(
                "E_SYNTAX", "模板输出结束时标签、属性或脚本块没有闭合",
                rs.last_tpl, rs.last_line, rs.last_col)


class _Missing:
    pass


_MISSING = _Missing()


def _collect_blocks(nodes, found):
    for node in nodes:
        if isinstance(node, Block):
            found[node.name] = node
        elif isinstance(node, If):
            for _, _, body in node.branches:
                _collect_blocks(body, found)
            _collect_blocks(node.else_body, found)
        elif isinstance(node, For):
            _collect_blocks(node.body, found)


def _to_string(value, node, tpl):
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        raise TemplateError("E_TYPE", "%s 是对象，不能直接输出" % node.path_str,
                            tpl, node.line, node.col)
    raise TemplateError("E_TYPE", "%s 是列表，不能直接输出" % node.path_str,
                        tpl, node.line, node.col)
