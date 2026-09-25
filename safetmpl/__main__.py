"""命令行：python -m safetmpl render <模板> <数据 JSON> <输出 HTML> / page <页面 HTML>"""

import os
import sys

from .engine import Engine, load_data
from .errors import TemplateError
from .page import build_page

USAGE = (
    "用法：\n"
    "  python -m safetmpl render <模板> <数据 JSON> <输出 HTML>\n"
    "  python -m safetmpl page <页面 HTML>"
)


def _write(path, text):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def cmd_render(tpl_path, data_path, out_path):
    engine = Engine()
    data = load_data(data_path)
    result = engine.render_file(tpl_path, data)
    _write(out_path, result.output)


def cmd_page(out_path):
    html = build_page("samples")
    _write(out_path, html)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) == 4 and argv[0] == "render":
            cmd_render(argv[1], argv[2], argv[3])
        elif len(argv) == 2 and argv[0] == "page":
            cmd_page(argv[1])
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except TemplateError as err:
        print(err.format(), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
