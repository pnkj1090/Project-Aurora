"""
Deterministic Mock Search Provider for testing.
Performs zero network access and supports flexible testing fixtures.
"""
from typing import List, Optional
from app.services.web.providers.base import BaseWebSearchProvider
from app.services.web.providers.models import WebSearchResponse, WebSearchResultItem


class MockWebSearchProvider(BaseWebSearchProvider):
    """
    Deterministic mock provider for unit and regression testing.
    """
    def __init__(
        self,
        fixed_results: Optional[List[WebSearchResultItem]] = None,
        should_fail: bool = False,
        failure_exception: Optional[Exception] = None,
        should_return_malformed: bool = False,
        malformed_type: str = "invalid_url",
    ):
        self.fixed_results = fixed_results
        self.should_fail = should_fail
        self.failure_exception = failure_exception or RuntimeError("Simulated search provider failure")
        self.should_return_malformed = should_return_malformed
        self.malformed_type = malformed_type
        self.recorded_queries: List[str] = []

    async def search(
        self,
        query: str,
        max_results: int = 3,
    ) -> WebSearchResponse:
        self.recorded_queries.append(query)

        if self.should_fail:
            raise self.failure_exception

        if self.should_return_malformed:
            if self.malformed_type == "invalid_url":
                return WebSearchResponse(
                    query=query,
                    results=[
                        WebSearchResultItem(
                            title="Malformed URL result",
                            url="ftp://forbidden.scheme/test",
                            snippet="Snippet with bad scheme",
                            domain="forbidden.scheme",
                        )
                    ],
                    total_results=1,
                )
            elif self.malformed_type == "excess_results":
                # Returns more items than requested
                items = [
                    WebSearchResultItem(
                        title=f"Result {i}",
                        url=f"https://example.com/res/{i}",
                        snippet=f"Snippet {i}",
                        domain="example.com",
                    )
                    for i in range(10)
                ]
                return WebSearchResponse(
                    query=query,
                    results=items,
                    total_results=len(items),
                )
            elif self.malformed_type == "leaked_secret":
                return WebSearchResponse(
                    query=query,
                    results=[
                        WebSearchResultItem(
                            title="Leaked token",
                            url="https://example.com/secret",
                            snippet="Contains AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6 credential",
                            domain="example.com",
                        )
                    ],
                    total_results=1,
                )

        if self.fixed_results is not None:
            results = self.fixed_results[:max_results]
            return WebSearchResponse(
                query=query,
                results=results,
                total_results=len(results),
            )

        # Default deterministic generator if no fixed results specified
        generated: List[WebSearchResultItem] = [
            WebSearchResultItem(
                title=f"Documentation for {query} - Page {i+1}",
                url=f"https://example.com/docs/{i+1}?q={query}",
                snippet=f"Comprehensive documentation and details regarding {query}.",
                domain="example.com",
                published_date="2026-01-15T12:00:00Z",
            )
            for i in range(max_results)
        ]

        return WebSearchResponse(
            query=query,
            results=generated,
            total_results=len(generated),
        )
