"""safetmpl：按上下文转义的模板引擎。"""

from .engine import Engine, RenderResult, load_data
from .errors import TemplateError

__all__ = ["Engine", "RenderResult", "TemplateError", "load_data"]
__version__ = "1.0.0"
