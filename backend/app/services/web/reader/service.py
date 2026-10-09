"""
Safe Web Reader Service for Project Aurora.
Orchestration layer coordinating Phase 5K.1 network security, Phase 5K.2 outbound privacy,
Phase 5K.2 in-memory cache, and Phase 5K.2 safe HTML extraction.
Does not act as an independent security authority; reuses verified subsystem components.
"""
from datetime import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.services.web.cache.models import CacheScope
from app.services.web.cache.policy import generate_cache_key
from app.services.web.cache.store import InMemoryWebCache
from app.services.web.extraction.base import BaseExtractionEngine
from app.services.web.extraction.engine import SafeHTMLExtractionEngine
from app.services.web.privacy.models import OutboundSearchRequest
from app.services.web.privacy.policy import (
    CREDENTIAL_PATTERNS,
    PROHIBITED_ID_PATTERNS,
)
from app.services.web.privacy.validator import validate_outbound_request
from app.services.web.providers.base import BaseWebSearchProvider
from app.services.web.providers.mock import MockWebSearchProvider
from app.services.web.providers.models import WebSearchResponse, WebSearchResultItem
from app.services.web.reader.models import WebPageReadResult, WebSearchToolResult
from app.services.web.security.config import WebSecurityConfig
from app.services.web.security.dns import BaseDNSResolver, get_validated_destination_ips
from app.services.web.security.transport import execute_secure_fetch
from app.services.web.security.url import validate_and_normalize_url

logger = logging.getLogger("aurora.web.reader")


class SafeWebReaderService:
    """
    Central orchestration service for web search and safe web page reading.
    Strictly coordinates existing security, privacy, and extraction modules.
    """

    def __init__(
        self,
        search_provider: Optional[BaseWebSearchProvider] = None,
        cache: Optional[InMemoryWebCache] = None,
        extraction_engine: Optional[BaseExtractionEngine] = None,
        dns_resolver: Optional[BaseDNSResolver] = None,
        security_config: Optional[WebSecurityConfig] = None,
    ):
        self.search_provider = search_provider or MockWebSearchProvider()
        self.cache = cache or InMemoryWebCache()
        self.extraction_engine = extraction_engine or SafeHTMLExtractionEngine()
        self.dns_resolver = dns_resolver
        self.security_config = security_config or WebSecurityConfig()

    async def read_page(
        self,
        url: str,
        user_id: str,
        bypass_cache: bool = False,
    ) -> WebPageReadResult:
        """
        Safely fetches and extracts readable text from a public web page.
        
        Execution Order:
        1. User ID validation (no default_user fallback).
        2. Basic string input validation.
        3. Phase 5K.1 URL canonicalization and port/scheme validation.
        4. Phase 5K.1 DNS resolution and all-or-nothing SSRF validation.
        5. Phase 5K.2 Outbound privacy check on target URL.
        6. Phase 5K.2 Page cache lookup (user_id + canonical_url).
        7. Phase 5K.1 Secure network transport (pinned IP, TLS, redirect budget, byte cap).
        8. Phase 5K.2 Safe HTML text extraction.
        9. Phase 5K.2 Page cache store.
        """
        # 1. Identity validation
        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id is required for user-partitioned web retrieval.")
        uid = user_id.strip()

        # 2. Input validation
        if not url or not isinstance(url, str) or not url.strip():
            raise ValueError("url must be a non-empty string.")

        # 3. Phase 5K.1 URL canonicalization & validation
        validated_url = validate_and_normalize_url(url.strip())

        # 4. Phase 5K.1 DNS & SSRF validation
        resolved_ips = await get_validated_destination_ips(
            validated_url,
            resolver=self.dns_resolver,
            timeout_seconds=self.security_config.connect_timeout_seconds,
        )

        # 5. Phase 5K.2 Outbound privacy check
        privacy_req = OutboundSearchRequest(raw_query=validated_url.canonical_url, user_context={})
        validate_outbound_request(privacy_req)

        # 6. Phase 5K.2 Page cache lookup
        cache_key = generate_cache_key(
            target=validated_url.canonical_url,
            scope=CacheScope.USER,
            user_id=uid,
        )

        if not bypass_cache:
            cached_entry = self.cache.get(cache_key, user_id=uid)
            if cached_entry and not cached_entry.is_expired:
                logger.info("Page cache hit for URL: %s (user: %s)", validated_url.canonical_url, uid)
                return WebPageReadResult(
                    url=cached_entry.canonical_url or validated_url.canonical_url,
                    title=cached_entry.title,
                    domain=cached_entry.domain or validated_url.hostname,
                    text=cached_entry.extracted_text or "",
                    character_count=len(cached_entry.extracted_text or ""),
                    is_truncated=False,
                    cached=True,
                )

        # 7. Phase 5K.1 Secure network transport
        response = await execute_secure_fetch(
            current_url=validated_url,
            current_ip=resolved_ips[0],
            config=self.security_config,
            resolver=self.dns_resolver,
        )

        # 8. Phase 5K.2 Safe HTML text extraction
        extracted = self.extraction_engine.extract_text_and_metadata(
            html_bytes=response.content,
            base_url=response.canonical_url,
            max_chars=self.security_config.max_text_extract_chars,
        )

        # 9. Phase 5K.2 Page cache store
        self.cache.put_content(
            target=validated_url.canonical_url,
            scope=CacheScope.USER,
            user_id=uid,
            canonical_url=response.canonical_url,
            title=extracted.title,
            domain=extracted.domain or validated_url.hostname,
            extracted_text=extracted.text,
            status_code=response.status_code,
        )

        return WebPageReadResult(
            url=response.canonical_url,
            title=extracted.title,
            domain=extracted.domain or validated_url.hostname,
            text=extracted.text,
            character_count=extracted.character_count,
            is_truncated=extracted.is_truncated,
            cached=False,
        )

    async def search(
        self,
        query: str,
        user_id: str,
        max_results: int = 3,
        user_context: Optional[Dict[str, Any]] = None,
    ) -> WebSearchToolResult:
        """
        Executes a web search with strict privacy boundary, cache lookup, and provider validation.
        
        Execution Order:
        1. User ID validation (no default_user fallback).
        2. Parameter validation (query non-empty, 1 <= max_results <= 5).
        3. Phase 5K.2 Outbound privacy transformation and fail-closed validation.
        4. Phase 5K.2 Search cache lookup (user_id + safe_query + max_results).
        5. BaseWebSearchProvider dispatch.
        6. Strict provider output validation.
        7. Phase 5K.2 Search cache store.
        """
        # 1. Identity validation
        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id is required for user-partitioned web retrieval.")
        uid = user_id.strip()

        # 2. Parameter validation
        if not query or not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string.")

        if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results < 1 or max_results > 5:
            raise ValueError(f"max_results must be an integer between 1 and 5 (got {max_results}).")

        # 3. Phase 5K.2 Privacy transformation & validation
        outbound_req = OutboundSearchRequest(
            raw_query=query.strip(),
            max_results=max_results,
            user_context=user_context or {},
        )
        safe_req = validate_outbound_request(outbound_req)
        safe_query = safe_req.query

        # 4. Phase 5K.2 Search cache lookup
        cache_target = f"q:{safe_query}:r:{max_results}"
        cache_key = generate_cache_key(
            target=cache_target,
            scope=CacheScope.USER,
            user_id=uid,
        )

        cached_entry = self.cache.get(cache_key, user_id=uid)
        if cached_entry and not cached_entry.is_expired and cached_entry.content_bytes:
            logger.info("Search cache hit for query: %s (user: %s)", safe_query, uid)
            try:
                raw_json = cached_entry.content_bytes.decode("utf-8")
                cached_items = [
                    WebSearchResultItem.model_validate_json(line)
                    for line in raw_json.splitlines()
                    if line.strip()
                ]
                return WebSearchToolResult(
                    query=safe_query,
                    results=cached_items,
                    total_results=len(cached_items),
                    cached=True,
                )
            except Exception as e:
                logger.warning("Failed to deserialize cached search entry: %s", e)

        # 5. Provider dispatch
        raw_response = await self.search_provider.search(
            query=safe_query,
            max_results=max_results,
        )

        # 6. Strict provider output validation
        validated_items = self._validate_provider_response(raw_response, max_results=max_results)

        # 7. Phase 5K.2 Search cache store
        serialized_lines = "\n".join(item.model_dump_json() for item in validated_items)
        self.cache.put_content(
            target=cache_target,
            scope=CacheScope.USER,
            user_id=uid,
            canonical_url="",
            content_bytes=serialized_lines.encode("utf-8"),
            title=f"Search: {safe_query}",
        )

        return WebSearchToolResult(
            query=safe_query,
            results=validated_items,
            total_results=len(validated_items),
            cached=False,
        )

    def _validate_provider_response(
        self,
        response: Any,
        max_results: int,
    ) -> List[WebSearchResultItem]:
        """
        Enforces strict, deterministic validation on outputs from search providers.
        
        Checks:
        1. Result count does not exceed max_results.
        2. URLs are valid HTTP/HTTPS, canonicalized through Phase 5K.1, ports 80/443 only,
           no userinfo, no fragments.
        3. Titles are bounded <= 500 chars, stripped of non-printable control chars.
        4. Snippets are bounded <= 2000 chars.
        5. Published dates conform to ISO-8601 formatting or None.
        6. Zero internal Aurora IDs or credentials leak in ANY string field (url, title,
           snippet, domain, published_date).
        """
        if not isinstance(response, WebSearchResponse) or not hasattr(response, "results") or not isinstance(response.results, list):
            raise ValueError("Malformed provider response: Expected WebSearchResponse with results list.")

        validated_items: List[WebSearchResultItem] = []

        for item in response.results:
            if len(validated_items) >= max_results:
                break

            if not isinstance(item, WebSearchResultItem):
                continue

            # 1. URL validation & canonicalization via Phase 5K.1
            try:
                validated_url = validate_and_normalize_url(item.url)
                canonical_url = validated_url.canonical_url
                domain = validated_url.hostname
            except Exception as e:
                logger.debug("Dropping provider result with invalid URL (%s): %s", item.url, e)
                continue

            # 2. Title validation and limits (max 500 chars, drop if exceeded)
            if not item.title or not isinstance(item.title, str):
                logger.debug("Dropping provider result with missing or non-string title.")
                continue

            if len(item.title) > 500:
                logger.debug("Dropping provider result with title exceeding 500 characters (%d chars).", len(item.title))
                continue

            clean_title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", item.title).strip()
            if not clean_title:
                logger.debug("Dropping provider result with empty title after stripping control characters.")
                continue

            # 3. Snippet validation and limits (max 2000 chars, drop if exceeded)
            clean_snippet = None
            if item.snippet is not None:
                if not isinstance(item.snippet, str):
                    logger.debug("Dropping provider result with non-string snippet.")
                    continue
                if len(item.snippet) > 2000:
                    logger.debug("Dropping provider result with snippet exceeding 2000 characters (%d chars).", len(item.snippet))
                    continue
                clean_snippet = re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", item.snippet).strip()

            # 4. Published date validation (valid ISO-8601 or None; drop if malformed string)
            clean_date = None
            if item.published_date is not None:
                if not isinstance(item.published_date, str):
                    logger.debug("Dropping provider result with non-string published_date.")
                    continue
                try:
                    datetime.fromisoformat(item.published_date.replace("Z", "+00:00"))
                    clean_date = item.published_date
                except (ValueError, TypeError):
                    logger.debug("Dropping provider result with malformed published_date: %s", item.published_date)
                    continue

            # 5. Security and leakage inspection on ALL externally returned string fields:
            # - url (raw item.url and canonical_url)
            # - title (raw item.title and clean_title)
            # - snippet (raw item.snippet and clean_snippet)
            # - domain (raw item.domain and canonical domain)
            # - published_date (raw item.published_date and clean_date)
            string_fields_to_inspect = [
                item.url,
                canonical_url,
                item.title,
                clean_title,
                domain or "",
                item.domain or "",
            ]
            if item.snippet:
                string_fields_to_inspect.append(item.snippet)
            if clean_snippet:
                string_fields_to_inspect.append(clean_snippet)
            if item.published_date:
                string_fields_to_inspect.append(item.published_date)
            if clean_date:
                string_fields_to_inspect.append(clean_date)

            has_leakage = False
            for field_val in string_fields_to_inspect:
                if not field_val:
                    continue
                for pat in PROHIBITED_ID_PATTERNS:
                    if pat.search(field_val):
                        has_leakage = True
                        break
                if has_leakage:
                    break
                for pat in CREDENTIAL_PATTERNS:
                    if pat.search(field_val):
                        has_leakage = True
                        break
                if has_leakage:
                    break

            if has_leakage:
                logger.warning("Dropping provider result containing internal ID or credential leakage.")
                continue

            validated_items.append(
                WebSearchResultItem(
                    title=clean_title,
                    url=canonical_url,
                    snippet=clean_snippet,
                    domain=domain,
                    published_date=clean_date,
                )
            )

        return validated_items
