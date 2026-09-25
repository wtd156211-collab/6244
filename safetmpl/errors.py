"""错误类型与格式化。

错误行格式：<路径>:<行>:<列>: <码>: <说明>，错在被展开的模板里时追加
"(来自 <展开者>:<行>)"，多层用 " <- " 连接、最内层在前。
"""


class TemplateError(Exception):
    """模板错误。code 取 E_SYNTAX/E_NAME/E_TPL/E_RECURSE/E_CTX/E_DATA/E_TYPE/E_IO。"""

    def __init__(self, code, message, tpl=None, line=None, col=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.tpl = tpl
        self.line = line
        self.col = col
        self.via = []  # [(tpl, line), ...] 最内层在前

    def __str__(self):
        if self.tpl is not None:
            text = "%s:%d:%d: %s: %s" % (self.tpl, self.line, self.col,
                                         self.code, self.message)
        else:
            text = "%s: %s" % (self.code, self.message)
        if self.via:
            chain = " <- ".join("%s:%d" % (t, ln) for t, ln in self.via)
            text += " (来自 %s)" % chain
        return text
