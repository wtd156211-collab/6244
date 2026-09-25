"""safetmpl 的 unittest 用例。只读 samples/，放大数据自备。"""

import copy
import json
import subprocess
import sys
import time
import tracemalloc
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from safetmpl import Engine, TemplateError, load_data  # noqa: E402
from safetmpl.page import build_page, count_hits  # noqa: E402

SAMPLES = ROOT / "samples"
# 错误行里的路径要与 samples/expected/*.err.txt 逐字节一致，
# 因此渲染调用一律用相对仓库根目录的 "samples/..." 形式（测试从仓库根目录跑）。
REL = Path("samples")
RENDER_OK = ["notice", "gallery", "scopes"]
RENDER_ERR = ["missing", "syntax", "ctx", "recurse", "type"]
INJECTION = ["quote", "angle", "scheme", "attr", "script"]
# README 第 6 节给出的值段条数（text/attr/url/script）与替换处数
SEG_COUNTS = {
    "notice": (12, 2, 5, 3, 28),
    "gallery": (6, 4, 3, 0, 12),
    "scopes": (16, 0, 0, 2, 10),
}


def render(name, record=False, engine=None):
    engine = engine or Engine()
    data = load_data(str(REL / "data" / f"{name}.json"))
    return engine.render_file(str(REL / "templates" / f"{name}.html"), data, record=record)


class TestRender(unittest.TestCase):
    def test_expected_outputs_byte_identical(self):
        for name in RENDER_OK:
            with self.subTest(name=name):
                result = render(name)
                expected = (SAMPLES / "expected" / f"{name}.html").read_bytes()
                self.assertEqual(result.output.encode("utf-8"), expected)

    def test_error_samples(self):
        for name in RENDER_ERR:
            with self.subTest(name=name):
                with self.assertRaises(TemplateError) as cm:
                    render(name)
                expected = (SAMPLES / "expected" / f"{name}.err.txt").read_text(
                    encoding="utf-8"
                ).rstrip("\n")
                self.assertEqual(cm.exception.format(), expected)

    def test_segment_counts_match_readme(self):
        for name, (n_text, n_attr, n_url, n_script, n_replaced) in SEG_COUNTS.items():
            with self.subTest(name=name):
                result = render(name, record=True)
                counts = {"text": 0, "attr": 0, "url": 0, "script": 0}
                replaced = 0
                for seg in result.segments:
                    if seg["kind"] == "value":
                        counts[seg["context"]] += 1
                        replaced += sum(n for _, _, n in seg["replaced"])
                self.assertEqual(
                    (counts["text"], counts["attr"], counts["url"], counts["script"], replaced),
                    (n_text, n_attr, n_url, n_script, n_replaced),
                )

    def test_deterministic_render(self):
        first = render("notice").output
        second = render("notice").output
        self.assertEqual(first, second)


class TestInjection(unittest.TestCase):
    def test_injection_outputs_byte_identical(self):
        engine = Engine()
        for name in INJECTION:
            with self.subTest(name=name):
                data = load_data(str(REL / "injection" / f"{name}.json"))
                result = engine.render_file(
                    str(REL / "injection" / f"{name}.html"), data, record=True
                )
                expected = (SAMPLES / "injection" / f"{name}.out.html").read_bytes()
                self.assertEqual(result.output.encode("utf-8"), expected)

    def test_no_executable_injection_in_value_segments(self):
        engine = Engine()
        for name in INJECTION:
            with self.subTest(name=name):
                data = load_data(str(REL / "injection" / f"{name}.json"))
                result = engine.render_file(
                    str(REL / "injection" / f"{name}.html"), data, record=True
                )
                hits = count_hits(result.segments)
                self.assertEqual(
                    hits, {"<": 0, ">": 0, "javascript:": 0, "</script": 0}
                )


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "safetmpl", *args],
            cwd=ROOT, capture_output=True, text=True,
        )

    def test_render_command(self):
        out = ROOT / "var" / "test_notice.html"
        out.unlink(missing_ok=True)
        proc = self.run_cli(
            "render", "samples/templates/notice.html",
            "samples/data/notice.json", str(out),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out.read_bytes(), (SAMPLES / "expected" / "notice.html").read_bytes())

    def test_error_exit_code_and_no_output(self):
        out = ROOT / "var" / "test_missing.html"
        out.unlink(missing_ok=True)
        proc = self.run_cli(
            "render", "samples/templates/missing.html",
            "samples/data/missing.json", str(out),
        )
        self.assertEqual(proc.returncode, 2)
        expected = (SAMPLES / "expected" / "missing.err.txt").read_text(encoding="utf-8")
        self.assertEqual(proc.stderr, expected)
        self.assertFalse(out.exists())

    def test_page_command_deterministic(self):
        first = self.run_cli("page", "var/test_page1.html")
        second = self.run_cli("page", "var/test_page2.html")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            (ROOT / "var" / "test_page1.html").read_bytes(),
            (ROOT / "var" / "test_page2.html").read_bytes(),
        )


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = build_page("samples")

    def test_required_markers(self):
        page = self.page
        for marker in (
            "标签之间", "属性", "链接地址", "脚本块",
            "literal", "escape_text", "escape_attr", "escape_url",
            "escape_script", "raw",
            "注入用例", "图例", "<svg",
        ):
            self.assertIn(marker, page)

    def test_error_lines_shown(self):
        for name in RENDER_ERR:
            line = (SAMPLES / "expected" / f"{name}.err.txt").read_text(
                encoding="utf-8"
            ).rstrip("\n")
            self.assertIn(line, self.page)

    def test_injection_hits_all_zero(self):
        self.assertIn("通过（全部为 0）", self.page)
        self.assertNotIn('<span class="fail">', self.page)

    def test_url_note_shown(self):
        self.assertIn("整串替换为 #", self.page)

    def test_deterministic(self):
        self.assertEqual(self.page, build_page("samples"))


class TestPerformance(unittest.TestCase):
    def test_repeated_render_parses_once(self):
        engine = Engine()
        base = load_data(str(REL / "data" / "notice.json"))
        start = time.perf_counter()
        rounds = 3000
        for i in range(rounds):
            data = copy.deepcopy(base)
            data["user"]["name"] = f"Amy <admin> #{i}"
            engine.render_file(str(REL / "templates" / "notice.html"), data)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, f"{rounds} 次渲染耗时 {elapsed:.2f}s")

    def test_peak_memory(self):
        engine = Engine()
        data = load_data(str(REL / "data" / "notice.json"))
        tpl = str(REL / "templates" / "notice.html")
        engine.render_file(tpl, data)  # 预热：解析与缓存不计入
        tracemalloc.start()
        baseline, _ = tracemalloc.get_traced_memory()
        for _ in range(1000):
            engine.render_file(tpl, data)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(peak - baseline, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
