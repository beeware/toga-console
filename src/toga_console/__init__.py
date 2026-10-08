from importlib.metadata import version

from .console import Console
from .model import Style

__version__ = version("toga-console")

__all__ = ["Console", "Style", "__version__"]
