"""渲染引擎：输出流状态机、作用域链、分段记录、模板缓存。"""

import json
import os
import re

from .errors import TemplateError
from .escape import URL_ATTRS, escape_attr, escape_script, escape_text, escape_url
from .parser import Block, For, If, Include, Text, Var, parse_template

__all__ = ["Engine", "RenderResult", "TemplateError", "load_data"]

MAX_DEPTH = 16


class StateMachine:
    """对着输出字符流跑的状态机：text / tag / attr / script。"""

    RE_TEXT = re.compile(r"<(?=[A-Za-z/])")
    RE_TAG = re.compile(r"[\">]")
    RE_SCRIPT_END = re.compile(r"</script", re.IGNORECASE)
    RE_TAGNAME = re.compile(r"<([A-Za-z][A-Za-z0-9]*)")
    RE_ATTR = re.compile(r"([A-Za-z_][-A-Za-z0-9_:.]*)\s*=\s*\Z")

    def __init__(self):
        self.state = "text"
        self.tag_name = ""
        self.attr_name = ""
        self._closing = False
        self._buf = ""

    def context(self):
        if self.state == "attr":
            return "url" if self.attr_name.lower() in URL_ATTRS else "attr"
        return self.state

    def feed(self, s, emit=None):
        i = 0
        n = len(s)
        while i < n:
            state = self.state
            if state == "text":
                m = self.RE_TEXT.search(s, i)
                if m is None:
                    if emit:
                        emit("text", s[i:])
                    break
                if m.start() > i and emit:
                    emit("text", s[i:m.start()])
                tm = self.RE_TAGNAME.match(s, m.start())
                if tm is not None:
                    self.tag_name = tm.group(1).lower()
                    self._closing = False
                else:
                    self.tag_name = ""
                    self._closing = True
                self._buf = ""
                self.state = "tag"
                i = m.start()
            elif state == "tag":
                m = self.RE_TAG.search(s, i)
                if m is None:
                    self._buf = (self._buf + s[i:])[-512:]
                    if emit:
                        emit("tag", s[i:])
                    break
                p = m.start()
                chunk = s[i:p]
                if s[p] == '"':
                    self._buf = (self._buf + chunk)[-512:]
                    if emit:
                        emit("tag", chunk)
                        emit("tag", '"')
                    am = self.RE_ATTR.search(self._buf)
                    self.attr_name = am.group(1) if am else ""
                    self._buf = (self._buf + '"')[-512:]
                    self.state = "attr"
                else:
                    self._buf = (self._buf + chunk)[-512:]
                    if emit:
                        emit("tag", chunk + ">")
                    if (
                        self.tag_name == "script"
                        and not self._closing
                        and not self._buf.rstrip().endswith("/")
                    ):
                        self.state = "script"
                    else:
                        self.state = "text"
                    self.tag_name = ""
                i = p + 1
            elif state == "attr":
                p = s.find('"', i)
                if p < 0:
                    if emit:
                        emit(self.context(), s[i:])
                    break
                if emit:
                    if p > i:
                        emit(self.context(), s[i:p])
                    emit("tag", '"')
                self._buf = (self._buf + s[i:p + 1])[-512:]
                self.state = "tag"
                i = p + 1
            else:
                m = self.RE_SCRIPT_END.search(s, i)
                if m is None:
                    if emit:
                        emit("script", s[i:])
                    break
                if emit:
                    if m.start() > i:
                        emit("script", s[i:m.start()])
                    emit("tag", m.group(0))
                self.state = "tag"
                self.tag_name = ""
                self._closing = True
                self._buf = m.group(0)
                i = m.end()


class RenderResult:
    __slots__ = ("output", "segments")

    def __init__(self, output, segments):
        self.output = output
        self.segments = segments


class _Context:
    def __init__(self, engine, record):
        self.engine = engine
        self.sm = StateMachine()
        self.out = []
        self.record = record
        self.segments = []
        self.stack = []
        self.blocks = None

    def emit_literal(self, text, tpl):
        if not text:
            return
        self.out.append(text)
        if self.record:
            self.sm.feed(text, lambda ctx, s: self._literal_piece(ctx, tpl, s))
        else:
            self.sm.feed(text)

    def _literal_piece(self, context, tpl, s):
        segs = self.segments
        if segs:
            last = segs[-1]
            if (
                last["kind"] == "literal"
                and last["context"] == context
                and last["tpl"] == tpl
            ):
                last["text"] += s
                return
        segs.append(
            {
                "kind": "literal",
                "context": context,
                "escape": "literal",
                "tpl": tpl,
                "path": "",
                "text": s,
                "replaced": [],
            }
        )

    def emit_value(self, raw, out, context, escape, tpl, path, replaced, note=None):
        self.out.append(out)
        self.sm.feed(out)
        if self.record:
            seg = {
                "kind": "value",
                "context": context,
                "escape": escape,
                "tpl": tpl,
                "path": path,
                "raw": raw,
                "text": out,
                "replaced": replaced,
            }
            if note:
                seg["note"] = note
            self.segments.append(seg)


def _resolve(segs, scopes, tpl, line, col, path_text):
    name = segs[0][1]
    for frame in reversed(scopes):
        if name in frame:
            value = frame[name]
            break
    else:
        raise TemplateError("E_DATA", f"缺少变量 {path_text}", tpl, line, col)
    for kind, key in segs[1:]:
        if kind == "name":
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                raise TemplateError(
                    "E_DATA", f"缺少变量 {path_text}", tpl, line, col
                )
        else:
            if isinstance(value, list):
                if 0 <= key < len(value):
                    value = value[key]
                else:
                    raise TemplateError(
                        "E_DATA", f"{path_text} 下标越界", tpl, line, col
                    )
            else:
                raise TemplateError(
                    "E_DATA", f"缺少变量 {path_text}", tpl, line, col
                )
    return value


def _to_string(value, path_text, tpl, line, col):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, dict):
        raise TemplateError(
            "E_TYPE", f"{path_text} 是对象，不能直接输出", tpl, line, col
        )
    if isinstance(value, list):
        raise TemplateError(
            "E_TYPE", f"{path_text} 是列表，不能直接输出", tpl, line, col
        )
    raise TemplateError(
        "E_TYPE", f"{path_text} 的类型不支持输出", tpl, line, col
    )


def _render_var(node, scopes, ctx, tpl):
    sm = ctx.sm
    if sm.state == "tag":
        raise TemplateError(
            "E_CTX", "变量只能放在用双引号括起来的属性值里", tpl, node.line, node.col
        )
    value = _resolve(node.segs, scopes, tpl, node.line, node.col, node.path_str)
    text = _to_string(value, node.path_str, tpl, node.line, node.col)
    if node.raw:
        if sm.state != "text":
            raise TemplateError(
                "E_CTX", "| raw 只能用在标签之间", tpl, node.line, node.col
            )
        ctx.emit_value(text, text, "text", "raw", tpl, node.path_str, [])
        return
    state = sm.state
    if state == "text":
        out, replaced = escape_text(text)
        ctx.emit_value(text, out, "text", "escape_text", tpl, node.path_str, replaced)
    elif state == "attr":
        if sm.attr_name.lower() in URL_ATTRS:
            out, replaced, note = escape_url(text)
            ctx.emit_value(
                text, out, "url", "escape_url", tpl, node.path_str, replaced, note
            )
        else:
            out, replaced = escape_attr(text)
            ctx.emit_value(
                text, out, "attr", "escape_attr", tpl, node.path_str, replaced
            )
    else:
        out, replaced = escape_script(text)
        ctx.emit_value(
            text, out, "script", "escape_script", tpl, node.path_str, replaced
        )


def _render_include(node, scopes, ctx, tpl):
    target = os.path.join(os.path.dirname(tpl), node.name)
    norm = os.path.normpath(target)
    if norm in ctx.stack:
        raise TemplateError(
            "E_RECURSE", f"模板展开成环：{target}", tpl, node.line, node.col
        )
    if len(ctx.stack) >= MAX_DEPTH:
        raise TemplateError(
            "E_RECURSE", f"模板展开超过 {MAX_DEPTH} 层：{target}",
            tpl, node.line, node.col,
        )
    try:
        frag = ctx.engine.get_template(target, from_node=(tpl, node.line, node.col))
    except TemplateError as err:
        if err.code != "E_TPL":
            err.via.append((tpl, node.line))
        raise
    ctx.stack.append(norm)
    try:
        _render_template(frag, scopes, ctx)
    except TemplateError as err:
        err.via.append((tpl, node.line))
        raise
    finally:
        ctx.stack.pop()


def _render_nodes(nodes, scopes, ctx, tpl):
    for node in nodes:
        if isinstance(node, Text):
            ctx.emit_literal(node.text, tpl)
        elif isinstance(node, Var):
            _render_var(node, scopes, ctx, tpl)
        elif isinstance(node, If):
            for segs, negate, body in node.branches:
                value = _resolve(segs, scopes, tpl, node.line, node.col, "")
                if bool(value) != negate:
                    _render_nodes(body, scopes, ctx, tpl)
                    break
            else:
                if node.else_body is not None:
                    _render_nodes(node.else_body, scopes, ctx, tpl)
        elif isinstance(node, For):
            value = _resolve(
                node.segs, scopes, tpl, node.line, node.col, node.path_str
            )
            if not isinstance(value, list):
                raise TemplateError(
                    "E_TYPE", f"{node.path_str} 不是列表，不能循环",
                    tpl, node.line, node.col,
                )
            count = len(value)
            frame = {}
            scopes.append(frame)
            try:
                for idx, item in enumerate(value):
                    frame.clear()
                    frame[node.var_name] = item
                    frame["loop"] = {
                        "index": idx + 1,
                        "first": idx == 0,
                        "last": idx == count - 1,
                    }
                    _render_nodes(node.body, scopes, ctx, tpl)
            finally:
                scopes.pop()
        elif isinstance(node, Include):
            _render_include(node, scopes, ctx, tpl)
        elif isinstance(node, Block):
            override = ctx.blocks.get(node.name) if ctx.blocks else None
            if override is not None:
                _render_nodes(override.body, scopes, ctx, override.tpl)
            else:
                _render_nodes(node.body, scopes, ctx, tpl)


def _render_template(tpl, scopes, ctx):
    if tpl.parent is not None:
        saved = ctx.blocks
        ctx.blocks = tpl.blocks
        try:
            _render_nodes(tpl.parent.nodes, scopes, ctx, tpl.parent.path)
        finally:
            ctx.blocks = saved
    else:
        _render_nodes(tpl.nodes, scopes, ctx, tpl.path)


def load_data(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise TemplateError("E_IO", f"读不到数据文件 {path}", path, 1, 1)
    except UnicodeDecodeError:
        raise TemplateError("E_IO", f"数据文件不是合法 UTF-8：{path}", path, 1, 1)
    except json.JSONDecodeError as err:
        raise TemplateError(
            "E_IO", f"数据 JSON 不合法：{err.msg}", path, err.lineno, err.colno
        )
    if not isinstance(data, dict):
        raise TemplateError("E_IO", "数据顶层必须是对象", path, 1, 1)
    return data


class Engine:
    """模板缓存 + 渲染入口。同一份模板只解析一次。"""

    def __init__(self):
        self.templates = {}

    def get_template(self, path, from_node=None):
        key = os.path.normpath(path)
        cached = self.templates.get(key)
        if cached is not None:
            return cached
        try:
            with open(path, encoding="utf-8") as f:
                src = f.read()
        except FileNotFoundError:
            raise self._error("E_TPL", f"找不到模板 {path}", path, from_node)
        except UnicodeDecodeError:
            raise self._error("E_IO", f"模板不是合法 UTF-8：{path}", path, from_node)
        except OSError:
            raise self._error("E_IO", f"读不到模板文件 {path}", path, from_node)
        tpl = parse_template(src, path)
        if tpl.extends_name is not None:
            parent_path = os.path.join(os.path.dirname(path), tpl.extends_name)
            parent = self.get_template(
                parent_path,
                from_node=(path, tpl.extends_node.line, tpl.extends_node.col),
            )
            if parent.extends_name is not None:
                raise TemplateError(
                    "E_SYNTAX", "不支持多级继承",
                    path, tpl.extends_node.line, tpl.extends_node.col,
                )
            for name, block in tpl.blocks.items():
                if name not in parent.blocks:
                    raise TemplateError(
                        "E_SYNTAX",
                        f'block "{name}" 在父模板 {tpl.extends_name} 里不存在',
                        path, block.line, block.col,
                    )
            tpl.parent = parent
        self.templates[key] = tpl
        return tpl

    @staticmethod
    def _error(code, message, path, from_node):
        if from_node is not None:
            return TemplateError(code, message, *from_node)
        return TemplateError(code, message, path, 1, 1)

    def render_file(self, path, data, record=False):
        tpl = self.get_template(path)
        ctx = _Context(self, record)
        ctx.stack.append(os.path.normpath(path))
        _render_template(tpl, [data], ctx)
        if ctx.sm.state != "text":
            raise TemplateError(
                "E_CTX",
                f"渲染结束时状态停在 {ctx.sm.state}，没有回到标签之间",
                tpl.path, tpl.end_line, tpl.end_col,
            )
        return RenderResult("".join(ctx.out), ctx.segments)
