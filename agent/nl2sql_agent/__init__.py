"""Natural-language-to-SQL agent built on LangChain and LangGraph."""

from .config import Settings
from .graph import Nl2SqlAgent
from .retrieval import KnowledgeBase

__version__ = "2.0.0"

__all__ = ["KnowledgeBase", "Nl2SqlAgent", "Settings", "__version__"]
