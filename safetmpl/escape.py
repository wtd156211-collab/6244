"""四种上下文的转义。"""

import re

__all__ = [
    "URL_ATTRS",
    "ALLOWED_SCHEMES",
    "escape_text",
    "escape_attr",
    "escape_url",
    "escape_script",
]

_TEXT_MAP = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}
_ATTR_MAP = {"&": "&amp;", '"': "&quot;", "<": "&lt;", ">": "&gt;"}
_TEXT_TRANSLATE = {ord(k): v for k, v in _TEXT_MAP.items()}
_ATTR_TRANSLATE = {ord(k): v for k, v in _ATTR_MAP.items()}
_NEED_TEXT = re.compile(r"[&<>]")
_NEED_ATTR = re.compile(r"[&<>\"]")
_NEED_SCRIPT = re.compile(r"[\"\\<>&]|[^ -~]")

URL_ATTRS = frozenset(
    {"href", "src", "action", "formaction", "poster", "cite", "xlink:href"}
)
ALLOWED_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

_STRIP_RE = re.compile(r"[\x00-\x20\x7f]+")
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*\Z")


def _counted(s, mapping):
    replaced = []
    for ch, rep in mapping.items():
        n = s.count(ch)
        if n:
            replaced.append([ch, rep, n])
    return replaced


def escape_text(s):
    """标签之间：& < >，不动引号。"""
    if not _NEED_TEXT.search(s):
        return s, []
    return s.translate(_TEXT_TRANSLATE), _counted(s, _TEXT_MAP)


def escape_attr(s):
    """双引号属性值：& " < >。"""
    if not _NEED_ATTR.search(s):
        return s, []
    return s.translate(_ATTR_TRANSLATE), _counted(s, _ATTR_MAP)


def escape_url(s):
    """链接地址：去控制字符与空白后判协议，不在放行名单整串换 #，否则按属性口径。"""
    stripped = _STRIP_RE.sub("", s)
    if ":" in stripped:
        scheme = stripped.split(":", 1)[0]
        if _SCHEME_RE.match(scheme) and scheme.lower() not in ALLOWED_SCHEMES:
            note = (
                f'链接协议 "{scheme.lower()}" 不在放行名单'
                "（http/https/mailto/tel），整串替换为 #"
            )
            return "#", [], note
    out, replaced = escape_attr(s)
    return out, replaced, None


def escape_script(s):
    """脚本块：JSON 字符串序列化，非 ASCII 与控制字符 \\uXXXX，< > & 也写 \\uXXXX。"""
    if not _NEED_SCRIPT.search(s):
        return '"' + s + '"', []
    out = ['"']
    counts = {}
    for ch in s:
        code = ord(ch)
        if ch == '"':
            rep = '\\"'
            out.append(rep)
            entry = counts.get(ch)
            if entry is None:
                counts[ch] = [ch, rep, 1]
            else:
                entry[2] += 1
        elif ch == "\\":
            rep = "\\\\"
            out.append(rep)
            entry = counts.get(ch)
            if entry is None:
                counts[ch] = [ch, rep, 1]
            else:
                entry[2] += 1
        elif ch in "<>&" or code < 0x20 or code > 0x7E:
            if code > 0xFFFF:
                v = code - 0x10000
                rep = "\\u%04x\\u%04x" % (0xD800 + (v >> 10), 0xDC00 + (v & 0x3FF))
            else:
                rep = "\\u%04x" % code
            out.append(rep)
            entry = counts.get(ch)
            if entry is None:
                counts[ch] = [ch, rep, 1]
            else:
                entry[2] += 1
        else:
            out.append(ch)
    out.append('"')
    return "".join(out), list(counts.values())
