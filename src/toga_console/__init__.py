from importlib.metadata import version

from .model import Style

__version__ = version("toga-console")

__all__ = ["Style", "__version__"]
