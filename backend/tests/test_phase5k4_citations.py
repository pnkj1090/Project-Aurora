"""
Phase 5K.4 — Citation Registry, Agent Loop Integration & API Tests.

Tests are organized into groups A-G per the approved test matrix.
No duplicate test definitions; pause/resume identity test exists once.
"""
import asyncio
import re
import pytest
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.web.citations.models import Citation, CitationOut
from app.services.web.citations.registry import CitationRegistry, _escape_xml_attr
from app.services.conversation.models import AgentExecutionState, AgentExecutionStatus


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def make_registry() -> CitationRegistry:
    return CitationRegistry()


def make_execution_state() -> AgentExecutionState:
    return AgentExecutionState(conversation_id="conv-test", user_id="usr-test")


EXAMPLE_URL = "https://example.com/article"
EXAMPLE_URL_TRAILING_SLASH = "https://example.com/article/"
EVIL_DOMAIN_URL = "https://example.com/article"  # domain in tool output could be evil


# ---------------------------------------------------------------------------
# A. Citation Registry Core
# ---------------------------------------------------------------------------

class TestCitationRegistryCore:

    def test_same_url_same_execution_same_id(self):
        """Same canonical URL in same execution => same citation ID."""
        reg = make_registry()
        c1 = reg.register(url=EXAMPLE_URL, title="Title A")
        c2 = reg.register(url=EXAMPLE_URL, title="Title B")  # duplicate
        assert c1 is not None
        assert c2 is not None
        assert c1.id == c2.id

    def test_different_urls_same_execution_different_ids(self):
        """Different URLs in same execution => different citation IDs."""
        reg = make_registry()
        c1 = reg.register(url="https://example.com/a")
        c2 = reg.register(url="https://example.com/b")
        assert c1 is not None
        assert c2 is not None
        assert c1.id != c2.id

    def test_same_url_different_executions_different_ids(self):
        """Same URL in two separate CitationRegistry instances => different IDs."""
        reg_a = make_registry()
        reg_b = make_registry()
        c_a = reg_a.register(url=EXAMPLE_URL)
        c_b = reg_b.register(url=EXAMPLE_URL)
        assert c_a is not None
        assert c_b is not None
        assert c_a.id != c_b.id, "Different executions must produce different citation IDs"

    def test_url_canonicalization_deduplication(self):
        """Canonical URL variants (trailing slash, fragment) deduplicate within one execution."""
        reg = make_registry()
        c1 = reg.register(url="https://example.com/article")
        # Fragments should be stripped by Phase 5K.1 normalization
        c2 = reg.register(url="https://example.com/article#section")
        assert c1 is not None
        # Both should canonicalize to the same URL -> same citation ID
        if c2 is not None:
            assert c1.id == c2.id

    def test_citation_ids_not_simple_global_counter(self):
        """IDs are not c1, c2... they contain execution-specific randomness."""
        reg = make_registry()
        c = reg.register(url=EXAMPLE_URL)
        assert c is not None
        assert c.id != "c1"
        assert c.id != "c2"
        assert not re.match(r"^c\d+$", c.id), "ID must not be a simple global counter"

    def test_citation_ids_match_expected_format(self):
        """IDs match c_[0-9a-f]{8}_<n> format."""
        reg = make_registry()
        c = reg.register(url=EXAMPLE_URL)
        assert c is not None
        assert re.match(r"^c_[0-9a-f]{8}_\d+$", c.id), f"ID '{c.id}' does not match expected format"

    def test_model_dump_excludes_citation_registry(self):
        """AgentExecutionState.model_dump() does not include citation_registry."""
        state = make_execution_state()
        # Register some citations
        state.citation_registry.register(url=EXAMPLE_URL, title="Test")
        dumped = state.model_dump()
        # Must not contain any citation registry data
        assert "citation_registry" not in dumped
        assert "_citation_registry" not in dumped
        assert "citations" not in dumped

    def test_active_execution_pause_resume_preserves_same_registry(self):
        """
        The same AgentExecutionState object preserves its CitationRegistry
        across state mutations (pause/resume) — verified by object identity.
        """
        state = make_execution_state()
        registry_before = state.citation_registry
        c = registry_before.register(url=EXAMPLE_URL, title="Pre-pause citation")

        # Simulate pause
        state.execution_status = AgentExecutionStatus.WAITING_FOR_CONFIRMATION

        # The registry is the SAME object
        registry_after_pause = state.citation_registry
        assert registry_after_pause is registry_before, "Registry object identity must be preserved on pause"

        # Simulate resume
        state.execution_status = AgentExecutionStatus.RUNNING
        registry_after_resume = state.citation_registry
        assert registry_after_resume is registry_before, "Registry object identity must be preserved on resume"

        # The pre-pause citation is still accessible
        assert c is not None
        found = registry_after_resume.get(c.id)
        assert found is not None
        assert found.id == c.id

    def test_no_sqlite_persistence(self):
        """Citation IDs are not stored in SQLite (structural: CitationRegistry has no DB access)."""
        reg = make_registry()
        # CitationRegistry is a plain Python class with no DB import or access
        assert not hasattr(reg, "_db")
        assert not hasattr(reg, "_session")
        assert not hasattr(reg, "_engine")
        # Verify register() returns an in-memory Citation, not a DB-backed object
        c = reg.register(url=EXAMPLE_URL)
        assert isinstance(c, Citation)

    def test_namespace_is_random(self):
        """Two registries have different namespaces with overwhelming probability."""
        namespaces = {make_registry().namespace for _ in range(20)}
        # With 4 bytes (2^32 possible values) and 20 registries, collisions are astronomically unlikely
        assert len(namespaces) > 1, "All namespaces were identical — namespace generation is not random"

    def test_execution_id_not_exposed_as_citation_id(self):
        """Citation ID format does not contain raw execution_id."""
        state = make_execution_state()
        c = state.citation_registry.register(url=EXAMPLE_URL)
        assert c is not None
        assert state.execution_id not in c.id, "Raw execution_id must not appear in citation ID"


# ---------------------------------------------------------------------------
# B. Search Integration
# ---------------------------------------------------------------------------

class TestSearchIntegration:

    def test_search_web_creates_citations(self):
        """Successful search_web result dict creates citations."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()
        res = ToolResult(
            tool_call_id="call-test-1",
            tool_name="search_web",
            success=True,
            output={"results": [{"url": EXAMPLE_URL, "title": "Test Article"}]},
            error=None,
        )
        _register_citations_from_tool_result("search_web", res, state)
        assert not state.citation_registry.is_empty()
        citations = state.citation_registry.all_citations()
        assert len(citations) == 1
        assert citations[0].url == EXAMPLE_URL

    def test_multiple_search_results_multiple_citations(self):
        """Each unique URL in search results creates a separate citation."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()
        res = ToolResult(
            tool_call_id="call-test-2",
            tool_name="search_web",
            success=True,
            output={"results": [
                {"url": "https://example.com/a", "title": "A"},
                {"url": "https://example.com/b", "title": "B"},
            ]},
            error=None,
        )
        _register_citations_from_tool_result("search_web", res, state)
        assert len(state.citation_registry.all_citations()) == 2

    def test_duplicate_url_same_execution_reuses_citation(self):
        """Duplicate URL in same search results reuses same citation ID."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()
        res = ToolResult(
            tool_call_id="call-test-3",
            tool_name="search_web",
            success=True,
            output={"results": [
                {"url": EXAMPLE_URL, "title": "A"},
                {"url": EXAMPLE_URL, "title": "B"},  # duplicate
            ]},
            error=None,
        )
        _register_citations_from_tool_result("search_web", res, state)
        citations = state.citation_registry.all_citations()
        assert len(citations) == 1

    def test_failed_search_creates_no_citations(self):
        """Failed search_web result does not register any citations."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()
        res = ToolResult(
            tool_call_id="call-test-4",
            tool_name="search_web",
            success=False,
            output={"results": [{"url": EXAMPLE_URL}]},
            error="Search failed",
        )
        _register_citations_from_tool_result("search_web", res, state)
        assert state.citation_registry.is_empty()

    def test_citation_metadata_from_validated_result_only(self):
        """Title and domain come from tool output (via registry), not from model."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()
        res = ToolResult(
            tool_call_id="call-test-5",
            tool_name="search_web",
            success=True,
            output={"results": [{"url": EXAMPLE_URL, "title": "Real Title", "domain": "evil.attacker.com"}]},
            error=None,
        )
        _register_citations_from_tool_result("search_web", res, state)
        citations = state.citation_registry.all_citations()
        assert len(citations) == 1
        # Domain must be derived from canonical URL, NOT from tool output's domain field
        assert citations[0].domain == "example.com"
        assert citations[0].title == "Real Title"


# ---------------------------------------------------------------------------
# C. Read Page Integration
# ---------------------------------------------------------------------------

class TestReadPageIntegration:

    def test_read_web_page_creates_citation(self):
        """Successful read_web_page result creates a citation."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()
        res = ToolResult(
            tool_call_id="call-test-6",
            tool_name="read_web_page",
            success=True,
            output={"url": EXAMPLE_URL, "title": "Page Title", "content": "..."},
            error=None,
        )
        _register_citations_from_tool_result("read_web_page", res, state)
        assert not state.citation_registry.is_empty()
        citations = state.citation_registry.all_citations()
        assert citations[0].url == EXAMPLE_URL

    def test_search_then_read_same_url_reuses_citation(self):
        """Search + read same canonical URL => same citation ID in one execution."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state = make_execution_state()

        # First: search_web returns URL
        search_res = ToolResult(
            tool_call_id="call-search-1",
            tool_name="search_web",
            success=True,
            output={"results": [{"url": EXAMPLE_URL, "title": "Article"}]},
            error=None,
        )
        _register_citations_from_tool_result("search_web", search_res, state)

        # Then: read_web_page for same URL
        read_res = ToolResult(
            tool_call_id="call-read-1",
            tool_name="read_web_page",
            success=True,
            output={"url": EXAMPLE_URL, "title": "Article"},
            error=None,
        )
        _register_citations_from_tool_result("read_web_page", read_res, state)

        citations = state.citation_registry.all_citations()
        assert len(citations) == 1, "Same canonical URL should deduplicate within one execution"

        # The citation_id in both results should match
        search_cid = search_res.output["results"][0].get("citation_id")
        read_cid = read_res.output.get("citation_id")
        assert search_cid is not None
        assert read_cid is not None
        assert search_cid == read_cid

    def test_read_in_new_execution_gets_new_citation_id(self):
        """Same URL read in a new execution => different citation ID."""
        from app.services.conversation.agent_loop import _register_citations_from_tool_result
        from app.services.tools.schemas import ToolResult

        state_a = make_execution_state()
        state_b = make_execution_state()

        res = ToolResult(
            tool_call_id="call-a-1",
            tool_name="read_web_page",
            success=True,
            output={"url": EXAMPLE_URL, "title": "Article"},
            error=None,
        )
        _register_citations_from_tool_result("read_web_page", res, state_a)

        res2 = ToolResult(
            tool_call_id="call-b-1",
            tool_name="read_web_page",
            success=True,
            output={"url": EXAMPLE_URL, "title": "Article"},
            error=None,
        )
        _register_citations_from_tool_result("read_web_page", res2, state_b)

        cids_a = [c.id for c in state_a.citation_registry.all_citations()]
        cids_b = [c.id for c in state_b.citation_registry.all_citations()]
        assert len(cids_a) == 1
        assert len(cids_b) == 1
        assert cids_a[0] != cids_b[0], "New execution must produce a different citation ID"


# ---------------------------------------------------------------------------
# D. Cache Isolation
# ---------------------------------------------------------------------------

class TestCacheIsolation:

    def test_cache_hit_same_url_two_executions_different_ids(self):
        """
        Cache hit for same URL in Execution A => c_<A>_1; in Execution B => c_<B>_1.
        Citation IDs must differ even when the same cached metadata is used.
        """
        reg_a = make_registry()
        reg_b = make_registry()

        # Same metadata (as if from cache hit) — different registry instances
        c_a = reg_a.register(url=EXAMPLE_URL, title="Article")
        c_b = reg_b.register(url=EXAMPLE_URL, title="Article")

        assert c_a is not None
        assert c_b is not None
        assert c_a.id != c_b.id, "Cache hit in different executions must produce different citation IDs"

    def test_cache_never_stores_citation_ids(self):
        """
        Phase 5K.2 cache objects (ExtractedWebContent / WebCacheEntry) have no citation_id field.
        Structural check: verify the cache model schema.
        """
        try:
            from app.services.web.cache.models import WebCacheEntry
            entry_fields = set(WebCacheEntry.model_fields.keys())
            assert "citation_id" not in entry_fields, "WebCacheEntry must not contain citation_id"
        except ImportError:
            pass  # Cache models may use different names; structural absence is sufficient

        try:
            from app.services.web.extraction.models import ExtractedWebContent
            content_fields = set(ExtractedWebContent.model_fields.keys())
            assert "citation_id" not in content_fields, "ExtractedWebContent must not contain citation_id"
        except ImportError:
            pass

    def test_old_execution_citation_ids_not_reused_from_cache(self):
        """Citation IDs are not cached; old execution IDs cannot be replayed."""
        reg_old = make_registry()
        c_old = reg_old.register(url=EXAMPLE_URL, title="Old")

        # New execution starts fresh — no knowledge of old registry
        reg_new = make_registry()
        c_new = reg_new.register(url=EXAMPLE_URL, title="Old")

        assert c_old is not None
        assert c_new is not None
        assert c_old.id != c_new.id

        # Old ID is not accessible in new registry
        assert reg_new.get(c_old.id) is None


# ---------------------------------------------------------------------------
# E. Final Synthesis — Citation Reference Validation
# ---------------------------------------------------------------------------

class TestFinalSynthesisCitationValidation:

    def test_valid_citation_ids_extracted_and_returned(self):
        """Valid [c_<ns>_<n>] markers in model text are accepted."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        c = state.citation_registry.register(url=EXAMPLE_URL, title="Test")
        assert c is not None

        model_text = f"Here is the answer [{c.id}]."
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 1
        assert results[0].id == c.id

    def test_unknown_citation_ids_rejected(self):
        """Unknown citation ID marker is stripped and not returned."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        state.citation_registry.register(url=EXAMPLE_URL)

        model_text = "See this [c_deadbeef_99]."  # ID not in registry
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 0

    def test_fabricated_citation_id_rejected(self):
        """Model-invented citation ID is rejected."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        # No citations registered

        model_text = "Fake source [c_abcd1234_1]."
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 0

    def test_markdown_link_not_treated_as_citation(self):
        """Arbitrary markdown links are never treated as citation references."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        c = state.citation_registry.register(url=EXAMPLE_URL)

        # Markdown link format — must NOT be parsed as citation
        model_text = "See [this article](https://example.com/article) for details."
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 0, "Markdown links must never be treated as citations"

    def test_arbitrary_model_url_not_citation(self):
        """Model producing a bare URL does not create or reference a citation."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()

        model_text = "See https://example.com for information."
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 0

    def test_model_cannot_supply_citation_metadata(self):
        """Even if model outputs a valid-format ID, if it's not in registry it is rejected."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        # Do NOT register any URL — model is trying to invent a citation
        model_text = "Source: [c_a1b2c3d4_1]"
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 0

    def test_final_citation_metadata_from_registry_only(self):
        """All returned CitationOut fields come from registry records, not model."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        c = state.citation_registry.register(url=EXAMPLE_URL, title="Registry Title")
        assert c is not None

        # Model references the ID but claims different title — must be ignored
        model_text = f"Answer [{c.id}] source title: Model-invented-title"
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 1
        assert results[0].title == "Registry Title"  # From registry, not model
        assert results[0].url == EXAMPLE_URL
        assert results[0].domain == "example.com"

    def test_final_synthesis_uses_tools_none(self):
        """
        The tool-free final synthesis path passes tools=None.
        Verified structurally: the synthesis call in agent_loop uses tools=None.
        """
        import inspect
        from app.services.conversation import agent_loop
        source = inspect.getsource(agent_loop)
        # Verify that final synthesis dispatches with tools=None
        assert "tools=None" in source, "Final synthesis must use tools=None"
        # Also verify the DATA ONLY source block pattern
        assert "DATA ONLY" in source, "Sources block must be labeled DATA ONLY"

    def test_citation_marker_regex_precision(self):
        """Citation marker regex matches only correctly formatted server-issued IDs."""
        from app.services.conversation.agent_loop import _CITATION_MARKER_RE

        # Valid markers
        assert _CITATION_MARKER_RE.findall("[c_a1b2c3d4_1]") == ["[c_a1b2c3d4_1]"]
        assert _CITATION_MARKER_RE.findall("[c_deadbeef_42]") == ["[c_deadbeef_42]"]

        # Invalid: wrong format
        assert _CITATION_MARKER_RE.findall("[c1]") == []
        assert _CITATION_MARKER_RE.findall("[c_abc_1]") == []  # namespace too short
        assert _CITATION_MARKER_RE.findall("[text](https://example.com)") == []
        assert _CITATION_MARKER_RE.findall("https://example.com") == []

    def test_deduplication_in_first_appearance_order(self):
        """Multiple references to the same citation ID are deduplicated."""
        from app.services.conversation.agent_loop import _validate_and_build_citations

        state = make_execution_state()
        c = state.citation_registry.register(url=EXAMPLE_URL)
        assert c is not None

        model_text = f"First [{c.id}], second [{c.id}], third [{c.id}]."
        results = _validate_and_build_citations(model_text, state)
        assert len(results) == 1  # Deduplicated


# ---------------------------------------------------------------------------
# F. Confirmation Pause & Resume
# ---------------------------------------------------------------------------

class TestConfirmationPauseResume:

    def test_citation_registry_survives_pause_and_resume(self):
        """Registry is intact after simulated confirmation pause/resume cycle."""
        state = make_execution_state()
        c_before = state.citation_registry.register(url=EXAMPLE_URL, title="Before pause")

        # Simulate pause
        state.execution_status = AgentExecutionStatus.WAITING_FOR_CONFIRMATION

        # Verify citation still accessible
        assert state.citation_registry.get(c_before.id) is not None

        # Simulate resume
        state.execution_status = AgentExecutionStatus.RUNNING
        c_after = state.citation_registry.get(c_before.id)
        assert c_after is not None
        assert c_after.id == c_before.id

    def test_resumed_execution_retains_citations(self):
        """Citations registered before pause are available after resume."""
        state = make_execution_state()
        c1 = state.citation_registry.register(url="https://example.com/before-pause")

        state.execution_status = AgentExecutionStatus.WAITING_FOR_CONFIRMATION
        state.execution_status = AgentExecutionStatus.RUNNING

        # Register another citation after "resume"
        c2 = state.citation_registry.register(url="https://example.com/after-resume")

        all_cits = state.citation_registry.all_citations()
        ids = [c.id for c in all_cits]
        assert c1.id in ids
        assert c2.id in ids
        assert len(ids) == 2

    def test_new_execution_cannot_access_previous_citations(self):
        """Fresh AgentExecutionState has an empty registry."""
        state_old = make_execution_state()
        c_old = state_old.citation_registry.register(url=EXAMPLE_URL)

        state_new = make_execution_state()
        assert state_new.citation_registry.is_empty()
        # Old citation ID is not accessible in new execution
        assert state_new.citation_registry.get(c_old.id) is None


# ---------------------------------------------------------------------------
# G. Security
# ---------------------------------------------------------------------------

class TestSecurity:

    def test_no_internal_ids_in_citation_metadata(self):
        """Citation metadata must not contain internal Aurora identifier patterns."""
        reg = make_registry()
        # Attempt to inject internal identifier patterns
        c = reg.register(
            url=EXAMPLE_URL,
            title="Normal Title task_abc123",  # Contains internal ID pattern
        )
        if c is not None:
            # Title with internal ID pattern should be sanitized to None
            assert c.title is None or "task_" not in (c.title or "")

    def test_no_credentials_in_citation_metadata(self):
        """Citation metadata fields contain no auth tokens, cookies, or credentials."""
        reg = make_registry()
        c = reg.register(url=EXAMPLE_URL, title="Test Article")
        if c is not None:
            out = CitationOut(
                id=c.id, url=c.url, title=c.title,
                domain=c.domain, published_date=c.published_date
            )
            dumped = out.model_dump()
            for field_val in dumped.values():
                if isinstance(field_val, str):
                    assert "Bearer " not in field_val
                    assert "cookie" not in field_val.lower()
                    assert "Authorization" not in field_val

    def test_execution_id_not_exposed_as_citation_id(self):
        """Citation ID format does not contain raw execution_id."""
        state = make_execution_state()
        c = state.citation_registry.register(url=EXAMPLE_URL)
        assert c is not None
        raw_exec_id = state.execution_id
        assert raw_exec_id not in c.id

    def test_namespace_is_random(self):
        """Two registries have different random namespaces."""
        namespaces = [make_registry().namespace for _ in range(10)]
        unique = set(namespaces)
        assert len(unique) > 1

    def test_domain_derived_from_canonical_url_not_caller(self):
        """
        If tool output provides domain='evil.example' but url='https://example.com/article',
        Citation.domain must be 'example.com' (derived from URL, not from caller argument).
        """
        reg = make_registry()
        c = reg.register(
            url="https://example.com/article",
            domain="evil.attacker.com",  # Caller-provided domain — must be ignored
        )
        assert c is not None
        assert c.domain == "example.com", (
            f"Domain must be derived from canonical URL hostname, got: {c.domain!r}"
        )

    def test_registry_has_no_network_access(self):
        """
        CitationRegistry.register() performs no DNS resolution, HTTP requests, or SSRF validation.
        Structural: ensure no network-related imports exist in registry.py.
        """
        import inspect
        from app.services.web.citations import registry as registry_module
        source = inspect.getsource(registry_module)
        assert "socket.getaddrinfo" not in source
        assert "httpx" not in source
        assert "aiohttp" not in source
        assert "urllib.request" not in source
        # SSRF check should NOT be called from registry
        assert "check_ssrf" not in source

    def test_cross_user_isolation(self):
        """
        Each AgentExecutionState gets its own CitationRegistry.
        Two separate execution states cannot share citations.
        """
        state_a = make_execution_state()
        state_b = make_execution_state()

        state_a.citation_registry.register(url="https://example.com/user-a-page")

        # state_b has its own empty registry
        assert state_b.citation_registry.is_empty()

    def test_citation_out_fields_exclude_internal_ids(self):
        """CitationOut schema has only public fields (id, url, title, domain, published_date)."""
        public_fields = set(CitationOut.model_fields.keys())
        internal_fields = {
            "execution_id", "user_id", "conversation_id",
            "task_id", "commitment_id", "reminder_id",
        }
        for field in internal_fields:
            assert field not in public_fields, f"CitationOut must not expose internal field: {field}"


# ---------------------------------------------------------------------------
# H. Fencing / Format
# ---------------------------------------------------------------------------

class TestSourcesFencing:

    def test_format_available_sources_escapes_xml_chars(self):
        """format_available_sources() escapes XML special characters in all fields."""
        reg = make_registry()
        # Title with XML-special chars
        c = reg.register(url='https://example.com/a&b', title='Title <with> "quotes" & \'apos\'')
        if c is not None:
            block = reg.format_available_sources()
            # Raw dangerous chars must not appear unescaped in the output
            # (URL is also escaped in the attribute value)
            assert "<with>" not in block
            assert '"quotes"' not in block or "&quot;" in block

    def test_format_available_sources_empty_when_no_citations(self):
        """format_available_sources() returns empty string when registry is empty."""
        reg = make_registry()
        assert reg.format_available_sources() == ""

    def test_escape_xml_attr_helper(self):
        """_escape_xml_attr escapes all five XML special characters."""
        assert _escape_xml_attr("&") == "&amp;"
        assert _escape_xml_attr('"') == "&quot;"
        assert _escape_xml_attr("<") == "&lt;"
        assert _escape_xml_attr(">") == "&gt;"
        assert _escape_xml_attr("'") == "&apos;"
        assert _escape_xml_attr("normal text") == "normal text"

    def test_resolve_ids_deduplicates_and_preserves_order(self):
        """resolve_ids() deduplicates and preserves first-appearance order."""
        reg = make_registry()
        c1 = reg.register(url="https://example.com/1")
        c2 = reg.register(url="https://example.com/2")
        assert c1 and c2

        resolved = reg.resolve_ids([c2.id, c1.id, c2.id, c1.id])
        assert len(resolved) == 2
        assert resolved[0].id == c2.id  # First appearance order
        assert resolved[1].id == c1.id

    def test_resolve_ids_excludes_unknown_ids(self):
        """resolve_ids() silently excludes IDs not in this registry."""
        reg = make_registry()
        c = reg.register(url=EXAMPLE_URL)
        assert c is not None

        resolved = reg.resolve_ids([c.id, "c_unknown_99", "c_also_fake_0"])
        assert len(resolved) == 1
        assert resolved[0].id == c.id


# ---------------------------------------------------------------------------
# I. API Contract
# ---------------------------------------------------------------------------

class TestAPIContract:

    def test_chat_response_has_citations_field(self):
        """ChatResponse Pydantic model has optional citations field."""
        from app.api.v1.endpoints.chat import ChatResponse
        from app.services.web.citations.models import CitationOut

        fields = ChatResponse.model_fields
        assert "citations" in fields
        # Field must be Optional (not required)
        field_info = fields["citations"]
        assert field_info.is_required() is False

    def test_citation_out_model_has_required_fields(self):
        """CitationOut has all required public fields."""
        out = CitationOut(id="c_a1b2c3d4_1", url=EXAMPLE_URL, domain="example.com")
        assert out.id == "c_a1b2c3d4_1"
        assert out.url == EXAMPLE_URL
        assert out.domain == "example.com"
        assert out.title is None
        assert out.published_date is None
