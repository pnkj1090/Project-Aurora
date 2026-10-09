import pytest
from typing import Optional, Any
from app.services.context.base import BaseContextProvider, ContextBlock
from app.services.context.engine import ContextEngine


class DummyPriorityProvider(BaseContextProvider):
    def __init__(self, name: str, priority: int, content: str):
        self._name = name
        self._priority = priority
        self._content = content

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    async def get_context(self, session: Optional[Any] = None, query: Optional[str] = None, **kwargs: Any) -> Optional[ContextBlock]:
        if not self._content:
            return None
        return ContextBlock(title=self._name.capitalize(), content=self._content, priority=self._priority)


class BrokenFailingProvider(BaseContextProvider):
    @property
    def name(self) -> str:
        return "broken_provider"

    @property
    def priority(self) -> int:
        return 5  # High priority to test that it fails before others run

    async def get_context(self, session: Optional[Any] = None, query: Optional[str] = None, **kwargs: Any) -> Optional[ContextBlock]:
        raise RuntimeError("Database connection suddenly dropped!")


@pytest.mark.asyncio
async def test_engine_deterministic_provider_ordering():
    # Register providers in reverse priority order
    p3 = DummyPriorityProvider(name="low_prio", priority=50, content="Third item")
    p1 = DummyPriorityProvider(name="high_prio", priority=10, content="First item")
    p2 = DummyPriorityProvider(name="mid_prio", priority=20, content="Second item")

    engine = ContextEngine(providers=[p3, p1, p2])

    blocks = await engine.gather_context_blocks()
    assert len(blocks) == 3
    assert blocks[0].title == "High_prio"
    assert blocks[1].title == "Mid_prio"
    assert blocks[2].title == "Low_prio"


@pytest.mark.asyncio
async def test_engine_omits_empty_blocks():
    p1 = DummyPriorityProvider(name="valid", priority=10, content="Real content")
    p2 = DummyPriorityProvider(name="empty_str", priority=20, content="   ")
    p3 = DummyPriorityProvider(name="none_block", priority=30, content="")

    engine = ContextEngine(providers=[p1, p2, p3])
    blocks = await engine.gather_context_blocks()

    assert len(blocks) == 1
    assert blocks[0].title == "Valid"


@pytest.mark.asyncio
async def test_engine_failure_isolation():
    failing = BrokenFailingProvider()
    healthy = DummyPriorityProvider(name="healthy", priority=20, content="Healthy data")

    engine = ContextEngine(providers=[failing, healthy])

    # Failing provider must NOT crash the engine
    blocks = await engine.gather_context_blocks()
    assert len(blocks) == 1
    assert blocks[0].title == "Healthy"

    # Prompt assembly also succeeds
    prompt = await engine.build_system_prompt()
    assert "Healthy data" in prompt


@pytest.mark.asyncio
async def test_engine_context_budget_enforcement():
    # Long block that exceeds max_block_chars
    long_content = "X" * 500
    p1 = DummyPriorityProvider(name="oversized_block", priority=10, content=long_content)

    # Secondary provider
    p2 = DummyPriorityProvider(name="second_block", priority=20, content="Important follow-up")

    # Engine with max_block_chars=100 and max_total_chars=150
    engine = ContextEngine(
        providers=[p1, p2],
        max_block_chars=100,
        max_total_chars=150
    )

    blocks = await engine.gather_context_blocks()
    # First block should be truncated
    assert len(blocks[0].content) <= 150
    assert "... [content truncated for budget]" in blocks[0].content

    # Total characters across all blocks should not exceed max_total_chars
    total_chars = sum(len(b.content) for b in blocks)
    assert total_chars <= 180  # Including truncation notice margin


@pytest.mark.asyncio
async def test_engine_build_system_prompt_formatting():
    p1 = DummyPriorityProvider(name="temporal", priority=10, content="Current Time: 12:00 UTC")
    p2 = DummyPriorityProvider(name="memory", priority=20, content="User likes green tea")

    engine = ContextEngine(providers=[p1, p2], base_instruction="You are Aurora.")
    prompt = await engine.build_system_prompt()

    # Verify structured layout
    assert prompt.startswith("You are Aurora.")
    assert "## Security & Data Isolation" in prompt
    assert "## Situational Context" in prompt
    assert "### Temporal" in prompt
    assert "Current Time: 12:00 UTC" in prompt
    assert "### Memory" in prompt
    assert "User likes green tea" in prompt
