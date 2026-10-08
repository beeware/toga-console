from importlib.metadata import version

from .console import Console
from .model import Style
from .sources import Attachment

__version__ = version("toga-console")

__all__ = ["Attachment", "Console", "Style", "__version__"]
