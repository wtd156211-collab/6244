"""safetmpl：按上下文转义的模板引擎。"""

from .engine import Engine, Segment
from .errors import TemplateError

__all__ = ["Engine", "Segment", "TemplateError"]
__version__ = "1.0.0"
