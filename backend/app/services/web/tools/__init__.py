"""
Web tools package for Project Aurora.
Exports search_web and read_web_page tools and registration helpers.
"""
from typing import Optional

from app.services.tools.registry import ToolRegistry
from app.services.web.reader.service import SafeWebReaderService
from app.services.web.tools.read import ReadWebPageTool
from app.services.web.tools.search import SearchWebTool


def register_web_tools(
    registry: ToolRegistry,
    reader_service: Optional[SafeWebReaderService] = None,
) -> None:
    """
    Registers search_web and read_web_page tools onto the provided ToolRegistry instance.
    """
    service = reader_service or SafeWebReaderService()
    registry.register(SearchWebTool(reader_service=service))
    registry.register(ReadWebPageTool(reader_service=service))


__all__ = [
    "SearchWebTool",
    "ReadWebPageTool",
    "register_web_tools",
]
