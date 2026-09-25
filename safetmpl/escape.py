"""四种上下文转义。每个函数返回 (写出文本, replaced[, note])，
replaced 每项为 [原字符, 替换后, 次数]，只记发生过的替换。"""

import re

# 标签之间：不动引号
_TEXT_TABLE = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))
# 双引号属性值
_ATTR_TABLE = (("&", "&amp;"), ('"', "&quot;"), ("<", "&lt;"), (">", "&gt;"))

_URL_ATTRS = frozenset(
    ("href", "src", "action", "formaction", "poster", "cite", "xlink:href"))

_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")
_ALLOWED_SCHEMES = frozenset(("http", "https", "mailto", "tel"))


def _escape_with_table(value, table):
    counts = []
    out = value
    for ch, entity in table:
        n = out.count(ch)
        counts.append(n)
        if n:
            out = out.replace(ch, entity)
    replaced = [[ch, ent, n] for (ch, ent), n in zip(table, counts) if n]
    return out, replaced


def escape_text(value):
    return _escape_with_table(value, _TEXT_TABLE)


def escape_attr(value):
    return _escape_with_table(value, _ATTR_TABLE)


def escape_url(value):
    """链接地址：去掉 ASCII 控制字符与空白后取 ':' 前那段判协议，
    放行 http/https/mailto/tel，否则整串换 '#'；放行或没有 ':' 的按属性口径转义。"""
    cleaned = "".join(c for c in value if ord(c) > 0x20 and ord(c) != 0x7F)
    note = None
    colon = cleaned.find(":")
    if colon >= 0:
        scheme = cleaned[:colon]
        if _SCHEME_RE.match(scheme):
            if scheme.lower() not in _ALLOWED_SCHEMES:
                note = ("协议 %s 不在放行名单（http/https/mailto/tel），"
                        "整串替换为 #" % scheme)
                return "#", [], note
        else:
            note = "协议段形状不是合法 scheme，按无协议地址放行"
    out, replaced = escape_attr(value)
    return out, replaced, note


def escape_script(value):
    """脚本块：JSON 字符串序列化（非 ASCII 与控制字符写 \\uXXXX），
    再把 <、>、& 写成 \\u003c、\\u003e、\\u0026。"""
    parts = ['"']
    counts = {}
    order = []

    def put(ch, rep):
        key = (ch, rep)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
        parts.append(rep)

    for ch in value:
        code = ord(ch)
        if ch == '"':
            put(ch, '\\"')
        elif ch == "\\":
            put(ch, "\\\\")
        elif ch == "<":
            put(ch, "\\u003c")
        elif ch == ">":
            put(ch, "\\u003e")
        elif ch == "&":
            put(ch, "\\u0026")
        elif code < 0x20 or code > 0x7E:
            if code > 0xFFFF:
                code -= 0x10000
                hi = 0xD800 + (code >> 10)
                lo = 0xDC00 + (code & 0x3FF)
                put(ch, "\\u%04x\\u%04x" % (hi, lo))
            else:
                put(ch, "\\u%04x" % code)
        else:
            parts.append(ch)
    parts.append('"')
    replaced = [[ch, rep, counts[(ch, rep)]] for ch, rep in order]
    return "".join(parts), replaced
