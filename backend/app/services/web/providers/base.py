"""
Vendor-neutral abstract base class for web search providers.
"""
from abc import ABC, abstractmethod
from app.services.web.providers.models import WebSearchResponse


class BaseWebSearchProvider(ABC):
    """
    Abstract interface for search providers.
    Decoupled from commercial engines and isolated from LLM agent direct access.
    """

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 3,
    ) -> WebSearchResponse:
        """
        Executes a web search with a validated, privacy-sanitized query.
        
        Args:
            query: Privacy-transformed public query.
            max_results: Clamped integer between 1 and 5.
            
        Returns:
            WebSearchResponse containing typed WebSearchResultItem elements.
        """
        pass
