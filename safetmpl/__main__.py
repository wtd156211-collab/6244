"""命令行：
python -m safetmpl render <模板> <数据 JSON> <输出 HTML>
python -m safetmpl page   <页面 HTML>
"""

import json
import os
import sys

from .engine import Engine
from .errors import TemplateError
from .page import write_page

_USAGE = ("用法：\n"
          "  python -m safetmpl render <模板> <数据 JSON> <输出 HTML>\n"
          "  python -m safetmpl page <页面 HTML>\n")


def _cmd_render(argv):
    if len(argv) != 3:
        sys.stderr.write(_USAGE)
        return 2
    tpl_path, data_path, out_path = argv
    try:
        if not os.path.isfile(tpl_path):
            raise TemplateError("E_IO", "读不到文件 %s" % tpl_path)
        try:
            with open(data_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            raise TemplateError("E_IO", "读不到文件或数据不合法：%s" % data_path)
        if not isinstance(data, dict):
            raise TemplateError("E_IO", "数据顶层必须是对象：%s" % data_path)
        engine = Engine()
        output, _ = engine.render(tpl_path, data)
    except TemplateError as err:
        sys.stderr.write(str(err) + "\n")
        return 2
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write(output)
    return 0


def _cmd_page(argv):
    if len(argv) != 1:
        sys.stderr.write(_USAGE)
        return 2
    try:
        write_page("samples", argv[0])
    except TemplateError as err:
        sys.stderr.write(str(err) + "\n")
        return 2
    except OSError as err:
        sys.stderr.write("E_IO: %s\n" % err)
        return 2
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        sys.stderr.write(_USAGE)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "render":
        return _cmd_render(rest)
    if command == "page":
        return _cmd_page(rest)
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
