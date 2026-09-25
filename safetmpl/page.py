"""展示页面：把全部入口模板与注入用例的渲染分段内联成一个自包含 HTML。"""

import html
import json
import os

from .engine import Engine
from .errors import TemplateError

_CONTEXT_LABEL = {
    "text": "标签之间",
    "attr": "属性",
    "url": "链接地址",
    "script": "脚本块",
    "tag": "标签结构",
}

_CONTEXT_COLOR = {
    "text": "#2563eb",
    "attr": "#d97706",
    "url": "#7c3aed",
    "script": "#dc2626",
    "tag": "#6b7280",
}

_CONTEXT_ORDER = ("text", "attr", "url", "script", "tag")

_HIT_NEEDLES = ("<", ">", "javascript:", "</script")

_CSS = """
body{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:2rem;color:#1f2937}
h1{font-size:1.5rem}h2{font-size:1.2rem;border-bottom:2px solid #e5e7eb;padding-bottom:.3rem;margin-top:2rem}
h3{font-size:1rem;margin-bottom:.3rem}
.meta{color:#6b7280;font-size:.85rem;margin:.2rem 0 .6rem}
table{border-collapse:collapse;width:100%;margin:.5rem 0 1rem}
th,td{border:1px solid #e5e7eb;padding:.3rem .5rem;font-size:.8rem;text-align:left;vertical-align:top}
th{background:#f9fafb}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:#f9fafb;border:1px solid #e5e7eb;padding:.6rem;overflow-x:auto;font-size:.8rem}
pre.err{background:#fef2f2;border-color:#fecaca;color:#b91c1c}
.badge{display:inline-block;padding:.05rem .45rem;border-radius:.6rem;color:#fff;font-size:.72rem;white-space:nowrap}
.esc{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.72rem;color:#374151}
.segtext{max-width:32rem;word-break:break-all;white-space:pre-wrap}
.counts{font-size:.85rem;margin:.4rem 0}
.ok{color:#15803d;font-weight:600}
.bad{color:#b91c1c;font-weight:600}
svg{display:block;margin:.3rem 0}
details{margin:.3rem 0 1rem}summary{cursor:pointer;font-size:.85rem;color:#374151}
""".strip()


def _esc(text):
    return html.escape(text, quote=True)


def _badge(context):
    return '<span class="badge" style="background:%s">%s</span>' % (
        _CONTEXT_COLOR[context], _CONTEXT_LABEL[context])


def _legend_svg():
    parts = ['<svg width="640" height="20" viewBox="0 0 640 20" '
             'xmlns="http://www.w3.org/2000/svg" role="img" '
             'aria-label="上下文图例">']
    x = 0
    for context in _CONTEXT_ORDER:
        label = _CONTEXT_LABEL[context]
        width = 16 + len(label) * 13 + 14
        parts.append('<rect x="%d" y="3" width="12" height="12" fill="%s"/>'
                     % (x, _CONTEXT_COLOR[context]))
        parts.append('<text x="%d" y="13" font-size="12" fill="#374151">%s'
                     '</text>' % (x + 16, label))
        x += width
    parts.append('</svg>')
    return "".join(parts)


def _bar_svg(segments):
    total = sum(len(seg.text) for seg in segments)
    parts = ['<svg width="640" height="16" viewBox="0 0 640 16" '
             'xmlns="http://www.w3.org/2000/svg" role="img" '
             'aria-label="按上下文着色的分段横条">']
    if total:
        x = 0.0
        for seg in segments:
            if not seg.text:
                continue
            width = 640.0 * len(seg.text) / total
            parts.append('<rect x="%.2f" y="0" width="%.2f" height="16" '
                         'fill="%s"><title>%s / %s</title></rect>' % (
                             x, width, _CONTEXT_COLOR[seg.context],
                             _CONTEXT_LABEL[seg.context], seg.escape))
            x += width
    parts.append('</svg>')
    return "".join(parts)


def _counts_line(segments):
    counts = {context: 0 for context in _CONTEXT_ORDER}
    replaced_total = 0
    for seg in segments:
        if seg.kind != "value":
            continue
        counts[seg.context] += 1
        for _, _, n in seg.replaced:
            replaced_total += n
    parts = " · ".join("%s %d" % (_CONTEXT_LABEL[c], counts[c])
                       for c in _CONTEXT_ORDER if counts[c])
    if not parts:
        parts = "无"
    return ('<p class="counts">值段：%s；替换 %d 处</p>'
            % (parts, replaced_total))


def _segment_table(segments):
    rows = ["<table><tr><th>#</th><th>类别</th><th>上下文</th><th>转义</th>"
            "<th>模板</th><th>路径</th><th>替换（原字符 → 替换后 × 次数）</th>"
            "<th>备注</th><th>写出文本</th></tr>"]
    for index, seg in enumerate(segments, 1):
        if seg.replaced:
            rep = "<br>".join(
                "%s → %s × %d" % (_esc(old), _esc(new), n)
                for old, new, n in seg.replaced)
        else:
            rep = "—"
        note = _esc(seg.note) if seg.note else "—"
        path = "<code>%s</code>" % _esc(seg.path) if seg.path else "—"
        shown = seg.text if len(seg.text) <= 400 else seg.text[:400] + "…"
        if seg.kind == "value":
            shown = "%s → %s" % (seg.raw, seg.text)
            if len(shown) > 400:
                shown = shown[:400] + "…"
        rows.append(
            "<tr><td>%d</td><td>%s</td><td>%s</td>"
            "<td><span class=\"esc\">%s</span></td>"
            "<td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td>"
            "<td class=\"segtext\"><code>%s</code></td></tr>" % (
                index, "值" if seg.kind == "value" else "字面",
                _badge(seg.context), seg.escape, _esc(seg.tpl), path,
                rep, note, _esc(shown)))
    rows.append("</table>")
    return "".join(rows)


def _sample_section(name, tpl_path, data_path, body):
    return ('<section><h3>%s</h3><p class="meta">模板 '
            '<code>%s</code> · 数据 <code>%s</code></p>%s</section>'
            % (_esc(name), _esc(tpl_path), _esc(data_path), body))


def _render_block(engine, tpl_path, data):
    out, segments = engine.render(tpl_path, data, collect=True)
    parts = [_bar_svg(segments), _counts_line(segments),
             _segment_table(segments),
             "<details><summary>完整渲染结果</summary><pre>%s</pre></details>"
             % _esc(out)]
    return "".join(parts), segments


def build_page(samples_dir):
    tpl_dir = os.path.join(samples_dir, "templates")
    data_dir = os.path.join(samples_dir, "data")
    inj_dir = os.path.join(samples_dir, "injection")

    entries = sorted(name[:-5] for name in os.listdir(tpl_dir)
                     if name.endswith(".html") and not name.startswith("_"))
    injections = []
    if os.path.isdir(inj_dir):
        injections = sorted(
            name[:-5] for name in os.listdir(inj_dir)
            if name.endswith(".html") and not name.endswith(".out.html"))

    sections = []
    for name in entries:
        tpl_path = os.path.join(tpl_dir, name + ".html")
        data_path = os.path.join(data_dir, name + ".json")
        with open(data_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        engine = Engine()
        try:
            body, _ = _render_block(engine, tpl_path, data)
        except TemplateError as err:
            body = '<pre class="err">%s</pre>' % _esc(str(err))
        sections.append(_sample_section(name, tpl_path, data_path, body))

    inj_sections = []
    for name in injections:
        tpl_path = os.path.join(inj_dir, name + ".html")
        data_path = os.path.join(inj_dir, name + ".json")
        with open(data_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        engine = Engine()
        try:
            body, segments = _render_block(engine, tpl_path, data)
        except TemplateError as err:
            body = '<pre class="err">%s</pre>' % _esc(str(err))
            segments = []
        hits = {needle: 0 for needle in _HIT_NEEDLES}
        for seg in segments:
            if seg.kind == "value":
                for needle in _HIT_NEEDLES:
                    hits[needle] += seg.text.count(needle)
        passed = all(count == 0 for count in hits.values())
        hit_text = " · ".join(
            "<code>%s</code> %d" % (_esc(needle), hits[needle])
            for needle in _HIT_NEEDLES)
        verdict = ('<span class="ok">通过</span>' if passed
                   else '<span class="bad">不通过</span>')
        body += ('<p class="counts">值段写出文本命中：%s — %s</p>'
                 % (hit_text, verdict))
        inj_sections.append(_sample_section(name, tpl_path, data_path, body))

    page = ["<!DOCTYPE html>", '<html lang="zh">', "<head>",
            '<meta charset="utf-8">',
            "<title>safetmpl 渲染分段报告</title>",
            "<style>", _CSS, "</style>", "</head>", "<body>",
            "<h1>safetmpl 渲染分段报告</h1>",
            "<p>每段输出的所属上下文与转义方式如下（颜色按上下文区分）：</p>",
            _legend_svg(),
            "<h2>入口模板</h2>"]
    page.extend(sections)
    page.append("<h2>注入用例</h2>")
    page.extend(inj_sections)
    page.extend(["</body>", "</html>", ""])
    return "\n".join(page)


def write_page(samples_dir, out_path):
    page = build_page(samples_dir)
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(page)
