"""词法与语法分析：把模板源码解析成节点树。"""

import re

from .errors import TemplateError

__all__ = [
    "Template",
    "Text",
    "Var",
    "If",
    "For",
    "Include",
    "Extends",
    "Block",
    "parse_template",
    "parse_path",
    "path_str",
]

_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_SEG_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]")
_FOR_RE = re.compile(r"for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(\S+)\Z")
_INCLUDE_RE = re.compile(r'include\s+"([^"\n]+)"\Z')
_EXTENDS_RE = re.compile(r'extends\s+"([^"\n]+)"\Z')
_BLOCK_RE = re.compile(r"block\s+([A-Za-z_][A-Za-z0-9_]*)\Z")


class Text:
    __slots__ = ("text", "line", "col")

    def __init__(self, text, line, col):
        self.text = text
        self.line = line
        self.col = col


class Var:
    __slots__ = ("segs", "path_str", "raw", "line", "col")

    def __init__(self, segs, path_str, raw, line, col):
        self.segs = segs
        self.path_str = path_str
        self.raw = raw
        self.line = line
        self.col = col


class If:
    __slots__ = ("branches", "else_body", "line", "col")

    def __init__(self, branches, else_body, line, col):
        self.branches = branches
        self.else_body = else_body
        self.line = line
        self.col = col


class For:
    __slots__ = ("var_name", "segs", "path_str", "body", "line", "col")

    def __init__(self, var_name, segs, path_str, body, line, col):
        self.var_name = var_name
        self.segs = segs
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


class Extends:
    __slots__ = ("name", "line", "col")

    def __init__(self, name, line, col):
        self.name = name
        self.line = line
        self.col = col


class Block:
    __slots__ = ("name", "body", "line", "col", "tpl")

    def __init__(self, name, body, line, col):
        self.name = name
        self.body = body
        self.line = line
        self.col = col
        self.tpl = None


class Template:
    __slots__ = (
        "path",
        "nodes",
        "extends_name",
        "extends_node",
        "blocks",
        "parent",
        "end_line",
        "end_col",
    )

    def __init__(self, path, nodes, extends_name, extends_node, blocks, end_line, end_col):
        self.path = path
        self.nodes = nodes
        self.extends_name = extends_name
        self.extends_node = extends_node
        self.blocks = blocks
        self.parent = None
        self.end_line = end_line
        self.end_col = end_col


def parse_path(text):
    """路径 = 名字 ('.' 名字 | '[' 整数 ']')*，不合法返回 None。"""
    m = _NAME_RE.match(text)
    if m is None:
        return None
    segs = [("name", m.group(0))]
    rest = text[m.end():]
    while rest:
        sm = _PATH_SEG_RE.match(rest)
        if sm is None:
            return None
        if sm.group(1) is not None:
            segs.append(("name", sm.group(1)))
        else:
            segs.append(("index", int(sm.group(2))))
        rest = rest[sm.end():]
    return segs


def path_str(segs):
    out = [segs[0][1]]
    for kind, key in segs[1:]:
        if kind == "name":
            out.append("." + key)
        else:
            out.append(f"[{key}]")
    return "".join(out)


def _advance(line, col, text):
    nl = text.count("\n")
    if nl:
        line += nl
        col = len(text) - text.rfind("\n")
    else:
        col += len(text)
    return line, col


def lex(src, path):
    """切成 text / var / stmt / comment 记号，行列按 Unicode 码点计、指向标签的 {。"""
    tokens = []
    i = 0
    n = len(src)
    line = 1
    col = 1
    while i < n:
        j = src.find("{", i)
        if j < 0:
            j = n
        if j > i:
            chunk = src[i:j]
            tokens.append(("text", chunk, line, col))
            line, col = _advance(line, col, chunk)
        if j >= n:
            break
        start_line, start_col = line, col
        two = src[j:j + 2]
        if two == "{{":
            kind, end = "var", "}}"
        elif two == "{%":
            kind, end = "stmt", "%}"
        elif two == "{#":
            kind, end = "comment", "#}"
        else:
            tokens.append(("text", "{", line, col))
            col += 1
            i = j + 1
            continue
        k = src.find(end, j + 2)
        if k < 0:
            raise TemplateError("E_SYNTAX", "标签没有闭合", path, start_line, start_col)
        tokens.append((kind, src[j + 2:k], start_line, start_col))
        line, col = _advance(line, col, src[j:k + 2])
        i = k + 2
    return tokens, line, col


class _Parser:
    def __init__(self, tokens, path):
        self.tokens = tokens
        self.path = path
        self.i = 0

    def run(self):
        nodes, _ = self.parse_until(())
        return nodes

    def parse_until(self, stops):
        nodes = []
        while self.i < len(self.tokens):
            kind, inner, line, col = self.tokens[self.i]
            if kind == "text":
                nodes.append(Text(inner, line, col))
                self.i += 1
            elif kind == "comment":
                self.i += 1
            elif kind == "var":
                nodes.append(self.parse_var(inner, line, col))
                self.i += 1
            else:
                word = inner.strip().split(None, 1)[0] if inner.strip() else ""
                if word in stops:
                    return nodes, (inner, line, col)
                nodes.append(self.parse_stmt(inner, line, col))
        return nodes, None

    def parse_var(self, inner, line, col):
        text = inner.strip()
        raw = False
        if "|" in text:
            parts = text.split("|")
            if len(parts) == 2 and parts[1].strip() == "raw":
                raw = True
                text = parts[0].strip()
            else:
                raise TemplateError(
                    "E_SYNTAX",
                    "表达式里有不支持的写法（只允许 路径 或 路径 | raw）",
                    self.path, line, col,
                )
        segs = parse_path(text)
        if segs is None:
            raise TemplateError(
                "E_SYNTAX",
                "表达式里有不支持的写法（只允许 路径 或 路径 | raw）",
                self.path, line, col,
            )
        return Var(segs, path_str(segs), raw, line, col)

    def parse_stmt(self, inner, line, col):
        self.i += 1
        text = inner.strip()
        word = text.split(None, 1)[0] if text else ""
        if word == "if":
            return self.parse_if(text[2:], line, col)
        if word == "for":
            return self.parse_for(text, line, col)
        if word == "include":
            return self.parse_include(text, line, col)
        if word == "extends":
            return self.parse_extends(text, line, col)
        if word == "block":
            return self.parse_block(text, line, col)
        if word in ("endif", "endfor", "endblock", "else", "elif"):
            raise TemplateError(
                "E_SYNTAX", f"{word} 没有匹配的开始标签", self.path, line, col
            )
        raise TemplateError(
            "E_SYNTAX", f"不支持的写法：{text}", self.path, line, col
        )

    def parse_cond(self, text, line, col):
        parts = text.split()
        negate = False
        if parts and parts[0] == "not":
            negate = True
            parts = parts[1:]
        segs = parse_path(parts[0]) if len(parts) == 1 else None
        if segs is None:
            raise TemplateError(
                "E_SYNTAX", "条件只允许 路径 或 not 路径", self.path, line, col
            )
        return segs, negate

    def parse_if(self, cond_text, line, col):
        segs, negate = self.parse_cond(cond_text, line, col)
        body, stop = self.parse_until(("elif", "else", "endif"))
        branches = [(segs, negate, body)]
        else_body = None
        while stop is not None:
            inner, stop_line, stop_col = stop
            text = inner.strip()
            word = text.split(None, 1)[0]
            self.i += 1
            if word == "elif":
                segs, negate = self.parse_cond(text[4:], stop_line, stop_col)
                body, stop = self.parse_until(("elif", "else", "endif"))
                branches.append((segs, negate, body))
            elif word == "else":
                if text != "else":
                    raise TemplateError(
                        "E_SYNTAX", "else 后面不能跟内容", self.path, stop_line, stop_col
                    )
                else_body, stop = self.parse_until(("endif",))
                if stop is None:
                    break
                if stop[0].strip() != "endif":
                    raise TemplateError(
                        "E_SYNTAX", "endif 后面不能跟内容",
                        self.path, stop[1], stop[2],
                    )
                self.i += 1
                return If(branches, else_body, line, col)
            else:
                if text != "endif":
                    raise TemplateError(
                        "E_SYNTAX", "endif 后面不能跟内容", self.path, stop_line, stop_col
                    )
                return If(branches, else_body, line, col)
        raise TemplateError(
            "E_SYNTAX", "if 没有对应的 endif", self.path, line, col
        )

    def parse_for(self, text, line, col):
        m = _FOR_RE.match(text)
        segs = parse_path(m.group(2)) if m else None
        if segs is None:
            raise TemplateError(
                "E_SYNTAX", "for 只支持 for 名字 in 路径", self.path, line, col
            )
        body, stop = self.parse_until(("endfor",))
        if stop is None:
            raise TemplateError(
                "E_SYNTAX", "for 没有对应的 endfor", self.path, line, col
            )
        if stop[0].strip() != "endfor":
            raise TemplateError(
                "E_SYNTAX", "endfor 后面不能跟内容", self.path, stop[1], stop[2]
            )
        self.i += 1
        return For(m.group(1), segs, path_str(segs), body, line, col)

    def parse_include(self, text, line, col):
        m = _INCLUDE_RE.match(text)
        if m is None:
            raise TemplateError(
                "E_SYNTAX", 'include 只支持 include "名字.html"（双引号）',
                self.path, line, col,
            )
        return Include(m.group(1), line, col)

    def parse_extends(self, text, line, col):
        m = _EXTENDS_RE.match(text)
        if m is None:
            raise TemplateError(
                "E_SYNTAX", 'extends 只支持 extends "名字.html"（双引号）',
                self.path, line, col,
            )
        return Extends(m.group(1), line, col)

    def parse_block(self, text, line, col):
        m = _BLOCK_RE.match(text)
        if m is None:
            raise TemplateError(
                "E_SYNTAX", "block 只支持 block 名字", self.path, line, col
            )
        body, stop = self.parse_until(("endblock", "block"))
        if stop is None:
            raise TemplateError(
                "E_SYNTAX", "block 没有对应的 endblock", self.path, line, col
            )
        if stop[0].strip().split(None, 1)[0] == "block":
            raise TemplateError(
                "E_SYNTAX", "block 不能嵌套", self.path, stop[1], stop[2]
            )
        if stop[0].strip() != "endblock":
            raise TemplateError(
                "E_SYNTAX", "endblock 后面不能跟内容", self.path, stop[1], stop[2]
            )
        self.i += 1
        return Block(m.group(1), body, line, col)


def parse_template(src, path):
    tokens, end_line, end_col = lex(src, path)
    nodes = _Parser(tokens, path).run()
    significant = [
        n for n in nodes if not (isinstance(n, Text) and n.text.strip() == "")
    ]
    extends_node = None
    blocks = {}
    for node in significant:
        if isinstance(node, Extends):
            if extends_node is not None:
                raise TemplateError(
                    "E_SYNTAX", "extends 只能出现一次", path, node.line, node.col
                )
            extends_node = node
        elif isinstance(node, Block):
            if node.name in blocks:
                raise TemplateError(
                    "E_SYNTAX", f'block "{node.name}" 重复定义',
                    path, node.line, node.col,
                )
            blocks[node.name] = node
            node.tpl = path
    if extends_node is not None:
        if significant[0] is not extends_node:
            raise TemplateError(
                "E_SYNTAX", "extends 必须是第一条语句",
                path, extends_node.line, extends_node.col,
            )
        for node in significant[1:]:
            if not isinstance(node, Block):
                raise TemplateError(
                    "E_SYNTAX",
                    "有 extends 的模板里，extends 与 block 之外不能有别的内容",
                    path, node.line, node.col,
                )
    return Template(
        path, nodes, extends_node.name if extends_node else None,
        extends_node, blocks, end_line, end_col,
    )
