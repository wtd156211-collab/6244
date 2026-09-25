"""模板解析：词法、语法、继承结构。解析成节点树，渲染时不再重复解析。"""

import re

from .errors import TemplateError

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_PATH_RE = re.compile(
    r"(%s)((?:\.%s|\[\d+\])*)\Z" % (_NAME, _NAME))
_SEG_RE = re.compile(r"\.(%s)|\[(\d+)\]" % _NAME)
_OPEN_RE = re.compile(r"\{\{|\{%|\{#")

_EXPR_MSG = "表达式里有不支持的写法（只允许 路径 或 路径 | raw）"


class Text:
    __slots__ = ("text", "line", "col")

    def __init__(self, text, line, col):
        self.text = text
        self.line = line
        self.col = col


class Var:
    __slots__ = ("path", "path_str", "raw", "line", "col")

    def __init__(self, path, path_str, raw, line, col):
        self.path = path          # (首名, (('key', 名) | ('idx', 整数), ...))
        self.path_str = path_str  # 模板里写出的原样路径
        self.raw = raw
        self.line = line
        self.col = col


class If:
    __slots__ = ("branches", "else_body", "line", "col")

    def __init__(self, branches, else_body, line, col):
        self.branches = branches  # [(path, negate, body), ...]
        self.else_body = else_body
        self.line = line
        self.col = col


class For:
    __slots__ = ("name", "path", "path_str", "body", "line", "col")

    def __init__(self, name, path, path_str, body, line, col):
        self.name = name
        self.path = path
        self.path_str = path_str
        self.body = body
        self.line = line
        self.col = col


class Include:
    __slots__ = ("name", "line", "col")

    def __init__(self, name, line, col):
        self.name = name
        self.line = line
        self.col = col


class Block:
    __slots__ = ("name", "body", "tpl", "line", "col")

    def __init__(self, name, body, tpl, line, col):
        self.name = name
        self.body = body
        self.tpl = tpl
        self.line = line
        self.col = col


class Extends:
    __slots__ = ("name", "line", "col")

    def __init__(self, name, line, col):
        self.name = name
        self.line = line
        self.col = col


def _lex(src, tpl):
    """切成 ('text'|'var'|'tag', 内容, 行, 列)；注释 {# … #} 直接丢弃。"""
    tokens = []
    pos = 0
    line = 1
    col = 1
    n = len(src)

    def advance(chunk):
        nonlocal line, col
        nl = chunk.count("\n")
        if nl:
            line += nl
            col = len(chunk) - chunk.rfind("\n")
        else:
            col += len(chunk)

    while pos < n:
        m = _OPEN_RE.search(src, pos)
        if m is None:
            advance(src[pos:])
            tokens.append(("text", src[pos:], line, col))
            break
        start = m.start()
        if start > pos:
            tokens.append(("text", src[pos:start], line, col))
            advance(src[pos:start])
        opener = m.group(0)
        closer = {"{{": "}}", "{%": "%}", "{#": "#}"}[opener]
        kind = {"{{": "var", "{%": "tag", "{#": "comment"}[opener]
        oline, ocol = line, col
        end = src.find(closer, start + 2)
        if end < 0:
            raise TemplateError("E_SYNTAX", "标签没有闭合，缺少 %s" % closer,
                                tpl, oline, ocol)
        content = src[start + 2:end]
        if kind != "comment":
            tokens.append((kind, content, oline, ocol))
        advance(src[start:end + 2])
        pos = end + 2
    return tokens


def _parse_path(expr, tpl, line, col, message=_EXPR_MSG):
    expr = expr.strip()
    m = _PATH_RE.match(expr)
    if m is None:
        raise TemplateError("E_SYNTAX", message, tpl, line, col)
    first = m.group(1)
    segs = []
    for sm in _SEG_RE.finditer(m.group(2)):
        if sm.group(1) is not None:
            segs.append(("key", sm.group(1)))
        else:
            segs.append(("idx", int(sm.group(2))))
    return (first, tuple(segs)), expr


def _parse_var(content, tpl, line, col):
    parts = content.split("|")
    if len(parts) > 2:
        raise TemplateError("E_SYNTAX", _EXPR_MSG, tpl, line, col)
    raw = False
    if len(parts) == 2:
        if parts[1].strip() != "raw":
            raise TemplateError("E_SYNTAX", _EXPR_MSG, tpl, line, col)
        raw = True
    path, path_str = _parse_path(parts[0], tpl, line, col)
    return Var(path, path_str, raw, line, col)


_COND_RE = re.compile(r"(not\s+)?(%s(?:\.%s|\[\d+\])*)\Z" % (_NAME, _NAME))


def _parse_cond(expr, tpl, line, col):
    expr = expr.strip()
    m = _COND_RE.match(expr)
    if m is None:
        raise TemplateError("E_SYNTAX",
                            "条件只支持 路径 或 not 路径", tpl, line, col)
    path, _ = _parse_path(expr[m.start(2):], tpl, line, col)
    return path, bool(m.group(1))


_FOR_RE = re.compile(r"(%s)\s+in\s+(\S(?:.*\S)?)\Z" % _NAME)
_QUOTED_RE = re.compile(r'"([^"]*)"\Z')


class _Parser:
    def __init__(self, tokens, tpl):
        self.tokens = tokens
        self.tpl = tpl
        self.pos = 0

    def parse_nodes(self, stop, opener=None):
        nodes = []
        while self.pos < len(self.tokens):
            kind, content, line, col = self.tokens[self.pos]
            if kind == "text":
                nodes.append(Text(content, line, col))
                self.pos += 1
                continue
            if kind == "var":
                nodes.append(_parse_var(content, self.tpl, line, col))
                self.pos += 1
                continue
            word = content.strip().split(None, 1)
            head = word[0] if word else ""
            rest = word[1] if len(word) > 1 else ""
            if head in stop:
                self.pos += 1
                return nodes, head, rest.strip(), line, col
            self.pos += 1
            if head == "if":
                nodes.append(self._parse_if(rest, line, col))
            elif head == "for":
                nodes.append(self._parse_for(rest, line, col))
            elif head == "include":
                nodes.append(self._parse_include(rest, line, col))
            elif head == "extends":
                nodes.append(self._parse_extends(rest, line, col))
            elif head == "block":
                nodes.append(self._parse_block(rest, line, col))
            elif head in ("endif", "elif", "else", "endfor", "endblock"):
                raise TemplateError(
                    "E_SYNTAX",
                    "{%% %s %%} 没有对应的开始标签" % head, self.tpl, line, col)
            else:
                raise TemplateError(
                    "E_SYNTAX", "不支持的标签 {%% %s %%}" % content.strip(),
                    self.tpl, line, col)
        if stop:
            raise TemplateError(
                "E_SYNTAX",
                "{%% %s %%} 没有对应的结束标签" % opener[0],
                self.tpl, opener[1], opener[2])
        return nodes, None, "", 0, 0

    def _parse_if(self, rest, line, col):
        cond = _parse_cond(rest, self.tpl, line, col)
        branches = []
        body, head, rest, hline, hcol = self.parse_nodes(
            ("elif", "else", "endif"), opener=("if", line, col))
        branches.append((cond[0], cond[1], body))
        else_body = []
        while head == "elif":
            cond = _parse_cond(rest, self.tpl, hline, hcol)
            body, head, rest, hline, hcol = self.parse_nodes(
                ("elif", "else", "endif"), opener=("if", line, col))
            branches.append((cond[0], cond[1], body))
        if head == "else":
            if rest:
                raise TemplateError("E_SYNTAX", "{% else %} 后面不能有内容",
                                    self.tpl, hline, hcol)
            body, head, rest, hline, hcol = self.parse_nodes(
                ("endif",), opener=("if", line, col))
            else_body = body
        return If(branches, else_body, line, col)

    def _parse_for(self, rest, line, col):
        m = _FOR_RE.match(rest.strip())
        if m is None:
            raise TemplateError(
                "E_SYNTAX", "循环只支持 {% for 名字 in 路径 %}",
                self.tpl, line, col)
        path, path_str = _parse_path(
            m.group(2), self.tpl, line, col,
            message="循环只支持 {% for 名字 in 路径 %}，不支持过滤/排序/解构")
        body, head, _, _, _ = self.parse_nodes(
            ("endfor",), opener=("for", line, col))
        return For(m.group(1), path, path_str, body, line, col)

    def _parse_include(self, rest, line, col):
        m = _QUOTED_RE.match(rest.strip())
        if m is None:
            raise TemplateError(
                "E_SYNTAX", 'include 只支持 {% include "名字.html" %}（双引号）',
                self.tpl, line, col)
        return Include(m.group(1), line, col)

    def _parse_extends(self, rest, line, col):
        m = _QUOTED_RE.match(rest.strip())
        if m is None:
            raise TemplateError(
                "E_SYNTAX", 'extends 只支持 {% extends "名字.html" %}（双引号）',
                self.tpl, line, col)
        return Extends(m.group(1), line, col)

    def _parse_block(self, rest, line, col):
        name = rest.strip()
        if not re.match(r"%s\Z" % _NAME, name):
            raise TemplateError("E_SYNTAX", "block 名不合法：%s" % name,
                                self.tpl, line, col)
        body, head, _, _, _ = self.parse_nodes(
            ("endblock",), opener=("block", line, col))
        for node in body:
            if isinstance(node, Block):
                raise TemplateError("E_SYNTAX", "block 不能嵌套",
                                    self.tpl, node.line, node.col)
        return Block(name, body, self.tpl, line, col)


def parse(src, tpl):
    """解析模板源码，返回节点列表。extends 的位置与继承合法性在这里校验。"""
    tokens = _lex(src, tpl)
    parser = _Parser(tokens, tpl)
    nodes, _, _, _, _ = parser.parse_nodes(())
    extends_at = None
    for i, node in enumerate(nodes):
        if isinstance(node, Extends):
            extends_at = i
            break
    if extends_at is not None:
        first = extends_at == 0 or (
            extends_at == 1 and isinstance(nodes[0], Text)
            and not nodes[0].text.strip())
        if not first:
            node = nodes[extends_at]
            raise TemplateError("E_SYNTAX", "extends 必须是第一条语句",
                                tpl, node.line, node.col)
    return nodes
