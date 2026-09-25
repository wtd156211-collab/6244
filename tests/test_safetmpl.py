"""safetmpl 测试：只读 samples/，放大数据在临时目录自备。"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safetmpl.engine import Engine
from safetmpl.errors import TemplateError
from safetmpl.page import build_page

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)  # 与 CLI 口径一致：在仓库根目录跑，路径用相对形式
SAMPLES = "samples"

RENDER_SAMPLES = ("notice", "gallery", "scopes")
ERROR_SAMPLES = ("missing", "syntax", "ctx", "recurse", "type")
INJECTION_SAMPLES = ("quote", "angle", "scheme", "attr", "script")

# README 第 6 节：值段条数与替换处数
SEGMENT_COUNTS = {
    "notice": ({"text": 12, "attr": 2, "url": 5, "script": 3}, 28),
    "gallery": ({"text": 6, "attr": 4, "url": 3, "script": 0}, 12),
    "scopes": ({"text": 16, "attr": 0, "url": 0, "script": 2}, 10),
}


def load_data(name, kind="data"):
    path = os.path.join(SAMPLES, kind, name + ".json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class RenderTest(unittest.TestCase):
    def test_render_samples_byte_identical(self):
        for name in RENDER_SAMPLES:
            with self.subTest(name=name):
                engine = Engine()
                out, _ = engine.render(
                    os.path.join(SAMPLES, "templates", name + ".html"),
                    load_data(name))
                with open(os.path.join(SAMPLES, "expected", name + ".html"),
                          encoding="utf-8") as fh:
                    self.assertEqual(out, fh.read())

    def test_error_samples_message(self):
        for name in ERROR_SAMPLES:
            with self.subTest(name=name):
                engine = Engine()
                with self.assertRaises(TemplateError) as ctx:
                    engine.render(
                        os.path.join(SAMPLES, "templates", name + ".html"),
                        load_data(name))
                with open(os.path.join(SAMPLES, "expected",
                                       name + ".err.txt"),
                          encoding="utf-8") as fh:
                    self.assertEqual(str(ctx.exception),
                                     fh.read().rstrip("\n"))

    def test_error_samples_cli_exit_code_and_stderr(self):
        for name in ERROR_SAMPLES:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    out_path = os.path.join(tmp, "out.html")
                    proc = subprocess.run(
                        [sys.executable, "-m", "safetmpl", "render",
                         os.path.join("samples", "templates", name + ".html"),
                         os.path.join("samples", "data", name + ".json"),
                         out_path],
                        cwd=ROOT, capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 2)
                    with open(os.path.join(SAMPLES, "expected",
                                           name + ".err.txt"),
                              encoding="utf-8") as fh:
                        self.assertEqual(proc.stderr, fh.read())
                    self.assertFalse(os.path.exists(out_path))

    def test_render_cli_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in RENDER_SAMPLES:
                with self.subTest(name=name):
                    out_path = os.path.join(tmp, name + ".html")
                    proc = subprocess.run(
                        [sys.executable, "-m", "safetmpl", "render",
                         os.path.join("samples", "templates", name + ".html"),
                         os.path.join("samples", "data", name + ".json"),
                         out_path],
                        cwd=ROOT, capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    with open(out_path, "rb") as fh:
                        actual = fh.read()
                    with open(os.path.join(SAMPLES, "expected",
                                           name + ".html"), "rb") as fh:
                        self.assertEqual(actual, fh.read())


class InjectionTest(unittest.TestCase):
    def test_injection_output_byte_identical(self):
        for name in INJECTION_SAMPLES:
            with self.subTest(name=name):
                engine = Engine()
                out, _ = engine.render(
                    os.path.join(SAMPLES, "injection", name + ".html"),
                    load_data(name, "injection"))
                with open(os.path.join(SAMPLES, "injection",
                                       name + ".out.html"),
                          encoding="utf-8") as fh:
                    self.assertEqual(out, fh.read())

    def test_injection_value_text_has_no_executable_markup(self):
        for name in INJECTION_SAMPLES:
            with self.subTest(name=name):
                engine = Engine()
                _, segments = engine.render(
                    os.path.join(SAMPLES, "injection", name + ".html"),
                    load_data(name, "injection"), collect=True)
                written = "".join(seg.text for seg in segments
                                  if seg.kind == "value")
                for needle in ("<", ">", "javascript:", "</script"):
                    self.assertEqual(written.count(needle), 0, needle)


class SegmentTest(unittest.TestCase):
    def test_value_segment_counts_match_readme(self):
        for name, (expected_counts, expected_replaced) in \
                SEGMENT_COUNTS.items():
            with self.subTest(name=name):
                engine = Engine()
                _, segments = engine.render(
                    os.path.join(SAMPLES, "templates", name + ".html"),
                    load_data(name), collect=True)
                counts = Counter(seg.context for seg in segments
                                 if seg.kind == "value")
                for context in ("text", "attr", "url", "script"):
                    self.assertEqual(counts.get(context, 0),
                                     expected_counts[context], context)
                replaced = sum(n for seg in segments if seg.kind == "value"
                               for _, _, n in seg.replaced)
                self.assertEqual(replaced, expected_replaced)

    def test_segments_cover_output_in_order(self):
        engine = Engine()
        out, segments = engine.render(
            os.path.join(SAMPLES, "templates", "notice.html"),
            load_data("notice"), collect=True)
        self.assertEqual("".join(seg.text for seg in segments), out)

    def test_url_replacement_carries_note(self):
        engine = Engine()
        _, segments = engine.render(
            os.path.join(SAMPLES, "injection", "scheme.html"),
            load_data("scheme", "injection"), collect=True)
        notes = [seg.note for seg in segments
                 if seg.kind == "value" and seg.context == "url"]
        self.assertEqual(len(notes), 6)
        self.assertTrue(all(note for note in notes[:3]))
        self.assertIsNone(notes[4])


class DeterminismTest(unittest.TestCase):
    def test_same_input_same_bytes(self):
        for name in RENDER_SAMPLES:
            with self.subTest(name=name):
                outputs = []
                for _ in range(2):
                    engine = Engine()
                    out, _ = engine.render(
                        os.path.join(SAMPLES, "templates", name + ".html"),
                        load_data(name))
                    outputs.append(out)
                self.assertEqual(outputs[0], outputs[1])

    def test_page_byte_identical_across_runs(self):
        first = build_page(SAMPLES)
        second = build_page(SAMPLES)
        self.assertEqual(first, second)


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = build_page(SAMPLES)

    def test_page_mentions_contexts_and_escapes(self):
        for needle in ("标签之间", "属性", "链接地址", "脚本块",
                       "literal", "escape_text", "escape_attr",
                       "escape_url", "escape_script", "raw"):
            self.assertIn(needle, self.page)

    def test_page_has_svg_bars_and_legend(self):
        self.assertIn("<svg", self.page)
        self.assertIn("图例", self.page)

    def test_page_shows_error_lines(self):
        for name in ERROR_SAMPLES:
            with self.subTest(name=name):
                with open(os.path.join(SAMPLES, "expected",
                                       name + ".err.txt"),
                          encoding="utf-8") as fh:
                    import html as html_mod
                    self.assertIn(
                        html_mod.escape(fh.read().rstrip("\n")), self.page)

    def test_page_injection_hit_counts_all_zero(self):
        self.assertIn("值段写出文本命中", self.page)
        self.assertNotIn("不通过", self.page)

    def test_page_segment_counts_line(self):
        self.assertIn("值段：标签之间 12 · 属性 2 · 链接地址 5 · 脚本块 3；"
                      "替换 28 处", self.page)


class PerformanceTest(unittest.TestCase):
    def _make_big(self, tmp, items):
        tpl = os.path.join(tmp, "big.html")
        with open(tpl, "w", encoding="utf-8") as fh:
            fh.write('<ul>\n{% for it in items %}<li>'
                     '<a href="{{ it.url }}">{{ it.t }}</a>'
                     '<i data-x="{{ it.t }}">{{ it.u }}</i>'
                     '</li>\n{% endfor %}</ul>\n<script>window.__x = '
                     '{{ items[0].t }};</script>\n')
        data = {"items": [
            {"t": "标题 & <%d>" % i, "u": "x\"%d" % i,
             "url": "https://example.test/p?a=%d&b=2" % i}
            for i in range(items)]}
        return tpl, data

    def test_big_loop_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpl, data = self._make_big(tmp, 10000)
            engine = Engine()
            start = time.perf_counter()
            out, _ = engine.render(tpl, data)
            elapsed = time.perf_counter() - start
            self.assertIn("<a href=\"https://example.test/p?a=1&amp;b=2\">"
                          "标题 &amp; &lt;1&gt;</a>", out)
            self.assertLess(elapsed, 3.0, "单模板 10^4 循环应在几秒内完成")

    def test_compile_once_render_many(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpl, data = self._make_big(tmp, 20)
            engine = Engine()
            rounds = 2000
            start = time.perf_counter()
            previous = None
            for i in range(rounds):
                data["items"][0]["t"] = "标题 & <%d>" % i
                out, _ = engine.render(tpl, data)
                if previous is not None and i == 1:
                    previous = out
            elapsed = time.perf_counter() - start
            self.assertLess(elapsed, 5.0, "2000 次渲染应在几秒内完成")
            self.assertEqual(len(engine.cache), 1,
                             "同一份模板不应重复解析")
            # 同一份数据跑两遍逐字节相同
            first, _ = engine.render(tpl, data)
            second, _ = engine.render(tpl, data)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
