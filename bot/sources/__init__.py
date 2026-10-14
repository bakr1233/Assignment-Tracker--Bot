"""Source plugins. Adding a site means adding one module here."""

from __future__ import annotations

from typing import Dict, Type

from .base import Source
from .blackboard import Blackboard
from .canvas import Canvas
from .gradescope import Gradescope
from .pearson import Pearson

REGISTRY: Dict[str, Type[Source]] = {
    cls.name: cls for cls in (Blackboard, Canvas, Gradescope, Pearson)
}

__all__ = ["REGISTRY", "Source"]
