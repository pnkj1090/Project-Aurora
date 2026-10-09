from app.services.conversation.service import ConversationService
from app.services.conversation.store import default_conversation_store
from app.services.conversation.context import ContextManager
from app.services.tools.registry import default_tool_registry
from app.services.tools.executor import ToolExecutor
from app.services.ai.factory import get_ai_provider


def get_conversation_service() -> ConversationService:
    """Dependency provider for FastAPI route handlers."""
    tool_reg = default_tool_registry
    return ConversationService(
        store=default_conversation_store,
        ai_provider=get_ai_provider(),
        context_manager=ContextManager(),
        tool_registry=tool_reg,
        tool_executor=ToolExecutor(registry=tool_reg),
    )
