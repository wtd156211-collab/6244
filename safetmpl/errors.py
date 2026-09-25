"""错误类型与格式化。"""


class TemplateError(Exception):
    """模板错误。格式：<路径>:<行>:<列>: <码>: <说明> [ (来自 <展开者>:<行> [<- ...]) ]"""

    def __init__(self, code, message, path, line, col):
        super().__init__(f"{path}:{line}:{col}: {code}: {message}")
        self.code = code
        self.message = message
        self.path = path
        self.line = line
        self.col = col
        self.via = []

    def format(self):
        text = f"{self.path}:{self.line}:{self.col}: {self.code}: {self.message}"
        if self.via:
            text += " (来自 " + " <- ".join(f"{p}:{ln}" for p, ln in self.via) + ")"
        return text
