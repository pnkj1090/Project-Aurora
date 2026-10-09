from typing import Optional, List


DEFAULT_SYSTEM_INSTRUCTION = (
    "You are Aurora, a thoughtful, proactive, and capable personal AI assistant. "
    "You communicate clearly, concisely, and helpfully. "
    "You maintain conversational context and remember what the user shares with you. "
    "When answering, be direct, accurate, and supportive. "
    "Do not pretend to perform real-world external actions unless a tool is provided."
)


class SystemPromptBuilder:
    """
    Builder pattern for modular system prompts.
    Provides clean extension points for future additions:
    - User preferences
    - Long-term memory retrieval
    - Environmental context (location, time, calendar)
    - Active tool definitions
    - Agent instructions
    """

    def __init__(self, base_instruction: str = DEFAULT_SYSTEM_INSTRUCTION):
        self.base_instruction = base_instruction
        self.context_blocks: List[str] = []

    def add_context_block(self, title: str, content: str) -> "SystemPromptBuilder":
        """Extension point for future Context Engine or Memory Engine data."""
        self.context_blocks.append(f"### {title}\n{content}")
        return self

    def build(self) -> str:
        if not self.context_blocks:
            return self.base_instruction

        additional = "\n\n".join(self.context_blocks)
        return f"{self.base_instruction}\n\n## Current Context\n{additional}"


def get_default_system_prompt() -> str:
    return SystemPromptBuilder().build()
