"""自包含展示页面：分段、上下文、转义方式、替换明细、注入判定。"""

import html
import os

from .engine import Engine, load_data
from .errors import TemplateError

__all__ = ["build_page", "count_hits"]

CONTEXT_STYLE = {
    "text": ("标签之间", "#1b7f4a"),
    "attr": ("属性", "#b26a00"),
    "url": ("链接地址", "#6d3bbd"),
    "script": ("脚本块", "#1456c7"),
    "tag": ("标签内", "#6b7280"),
}
CONTEXT_ORDER = ("text", "attr", "url", "script")

CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:24px;color:#1f2430;line-height:1.5}
h1{font-size:22px}h2{font-size:18px;margin:24px 0 8px}h3{font-size:15px;margin:18px 0 6px}
.meta{color:#4b5563;font-size:13px;margin:4px 0}
table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0}
th,td{border:1px solid #d7dbe3;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f2f4f8}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;word-break:break-all}
td code{white-space:pre-wrap}
.dot{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
pre.err{background:#fdf0f0;border:1px solid #e5b4b4;color:#8f1d1d;padding:8px 10px;font-size:12.5px;overflow:auto}
section.sample{border-top:2px solid #d7dbe3;padding-top:4px;margin-top:20px}
svg{display:block;margin:6px 0}
.pass{color:#1b7f4a;font-weight:600}
.fail{color:#b91c1c;font-weight:600}
"""


def _clip(s, limit=120):
    return s if len(s) <= limit else s[:limit] + "…"


def count_hits(segments):
    """值段写出文本里 < > javascript: </script 的命中次数。"""
    text = "".join(s["text"] for s in segments if s["kind"] == "value")
    low = text.lower()
    return {
        "<": text.count("<"),
        ">": text.count(">"),
        "javascript:": low.count("javascript:"),
        "</script": low.count("</script"),
    }


def _legend_svg():
    parts = []
    x = 0
    for key in ("text", "attr", "url", "script", "tag"):
        label, color = CONTEXT_STYLE[key]
        parts.append(f'<rect x="{x}" y="4" width="14" height="14" rx="2" fill="{color}"/>')
        parts.append(f'<text x="{x + 20}" y="16" font-size="13" fill="#1f2430">{label}</text>')
        x += 20 + len(label) * 14 + 22
    return f'<svg viewBox="0 0 {x} 24" width="{x}" height="24" role="img" aria-label="图例">{"".join(parts)}</svg>'


def _bar_svg(segments):
    total = sum(max(1, len(s["text"])) for s in segments) or 1
    width = 640.0
    x = 0.0
    rects = []
    for seg in segments:
        w = max(2.0, len(seg["text"]) * width / total)
        color = CONTEXT_STYLE[seg["context"]][1]
        rects.append(
            f'<rect x="{x:.2f}" y="0" width="{w:.2f}" height="16" fill="{color}"/>'
        )
        x += w
    return (
        f'<svg viewBox="0 0 {x:.2f} 16" width="640" height="16" role="img" '
        f'aria-label="分段横条">{"".join(rects)}</svg>'
    )


def _summary(segments):
    counts = {key: 0 for key in CONTEXT_ORDER}
    replaced = 0
    for seg in segments:
        if seg["kind"] == "value":
            counts[seg["context"]] += 1
            replaced += sum(n for _, _, n in seg["replaced"])
    parts = " · ".join(
        f"{CONTEXT_STYLE[key][0]} {counts[key]}" for key in CONTEXT_ORDER
    )
    return f"值段：{parts} ｜ 替换 {replaced} 处 ｜ 分段共 {len(segments)} 段"


def _segment_table(segments):
    rows = []
    for i, seg in enumerate(segments, 1):
        label, color = CONTEXT_STYLE[seg["context"]]
        dot = f'<span class="dot" style="background:{color}"></span>'
        replaced = "；".join(
            f"{html.escape(a)} → {html.escape(b)} ×{n}" for a, b, n in seg["replaced"]
        ) or "—"
        note = html.escape(seg.get("note", "")) or "—"
        if seg["kind"] == "value":
            path = html.escape(seg["path"])
            raw = html.escape(_clip(seg["raw"]))
        else:
            path = "—"
            raw = "—"
        text = html.escape(_clip(seg["text"]))
        rows.append(
            f'<tr><td>{i}</td><td>{html.escape(os.path.basename(seg["tpl"]))}</td>'
            f"<td>{dot}{label}</td>"
            f'<td><code>{seg["escape"]}</code></td>'
            f"<td><code>{path}</code></td>"
            f"<td><code>{raw}</code></td>"
            f"<td><code>{text}</code></td>"
            f"<td>{replaced}</td><td>{note}</td></tr>"
        )
    return (
        "<table><thead><tr><th>#</th><th>模板</th><th>上下文</th><th>转义</th>"
        "<th>路径</th><th>原值</th><th>写出</th><th>替换（前 → 后 × 次数）</th>"
        "<th>备注</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _sample_section(name, tpl_path, data_path, result):
    return (
        f'<section class="sample" id="sample-{name}">'
        f"<h2>样例 {html.escape(name)}</h2>"
        f'<p class="meta">模板 <code>{html.escape(tpl_path)}</code> · 数据 '
        f"<code>{html.escape(data_path)}</code></p>"
        f'<p class="meta">{html.escape(_summary(result.segments))}</p>'
        f"{_bar_svg(result.segments)}"
        f"{_segment_table(result.segments)}"
        "</section>"
    )


def _error_section(name, tpl_path, data_path, err):
    return (
        f'<section class="sample" id="sample-{name}">'
        f"<h2>样例 {html.escape(name)}（报错）</h2>"
        f'<p class="meta">模板 <code>{html.escape(tpl_path)}</code> · 数据 '
        f"<code>{html.escape(data_path)}</code></p>"
        f'<pre class="err">{html.escape(err.format())}</pre>'
        "</section>"
    )


def _injection_section(name, tpl_path, data_path, result):
    hits = count_hits(result.segments)
    ok = all(v == 0 for v in hits.values())
    hit_text = " · ".join(
        f"{html.escape(k)} {v}" for k, v in hits.items()
    )
    verdict = (
        '<span class="pass">通过（全部为 0）</span>'
        if ok
        else '<span class="fail">失败</span>'
    )
    return (
        f'<section class="sample" id="injection-{name}">'
        f"<h3>注入用例 {html.escape(name)}</h3>"
        f'<p class="meta">模板 <code>{html.escape(tpl_path)}</code> · 数据 '
        f"<code>{html.escape(data_path)}</code></p>"
        f'<p class="meta">值段写出文本命中：{hit_text} ｜ 结论：{verdict}</p>'
        f'<p class="meta">{html.escape(_summary(result.segments))}</p>'
        f"{_bar_svg(result.segments)}"
        f"{_segment_table(result.segments)}"
        "</section>"
    )


def build_page(samples_dir):
    engine = Engine()
    tpl_dir = os.path.join(samples_dir, "templates")
    data_dir = os.path.join(samples_dir, "data")
    inj_dir = os.path.join(samples_dir, "injection")

    body = []
    for fname in sorted(os.listdir(tpl_dir)):
        if fname.startswith("_") or not fname.endswith(".html"):
            continue
        name = fname[:-5]
        tpl_path = os.path.join(tpl_dir, fname)
        data_path = os.path.join(data_dir, name + ".json")
        data = load_data(data_path)
        try:
            result = engine.render_file(tpl_path, data, record=True)
        except TemplateError as err:
            body.append(_error_section(name, tpl_path, data_path, err))
        else:
            body.append(_sample_section(name, tpl_path, data_path, result))

    injection = []
    for fname in sorted(os.listdir(inj_dir)):
        if not fname.endswith(".html") or fname.endswith(".out.html"):
            continue
        name = fname[:-5]
        tpl_path = os.path.join(inj_dir, fname)
        data_path = os.path.join(inj_dir, name + ".json")
        data = load_data(data_path)
        result = engine.render_file(tpl_path, data, record=True)
        injection.append(_injection_section(name, tpl_path, data_path, result))

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh">\n<head>\n<meta charset="utf-8">\n'
        "<title>safetmpl 渲染分段报告</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        "<h1>safetmpl 渲染分段报告</h1>\n"
        '<p class="meta">每段标出所属上下文（标签之间／属性／链接地址／脚本块）'
        "与实际用到的转义方式（literal／escape_text／escape_attr／escape_url／"
        "escape_script／raw）；横条按上下文着色，长度与该段写出字符数成正比。</p>\n"
        f"{_legend_svg()}\n"
        + "".join(body)
        + '<section class="sample" id="injection"><h2>注入用例</h2>\n'
        '<p class="meta">判定：值段写出文本里 <code>&lt;</code>、<code>&gt;</code>、'
        "<code>javascript:</code>、<code>&lt;/script</code> 的命中次数必须全部为 0。</p>\n"
        + "".join(injection)
        + "</section>\n</body>\n</html>\n"
    )
