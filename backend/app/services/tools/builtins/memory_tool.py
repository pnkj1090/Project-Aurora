import logging
from typing import Dict, Any, Optional, List

from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.tools.schemas import ToolCall, PendingConfirmation
from app.services.memory import get_memory_manager, MemoryManager, MemoryType

logger = logging.getLogger("aurora.tools.memory")


class StoreUserMemoryTool(BaseTool):
    """
    Tool allowing Aurora to store explicit facts, preferences, tasks, commitments,
    or episodic notes about the user into long-term memory.
    """

    def __init__(self, memory_manager: Optional[MemoryManager] = None):
        self._manager = memory_manager

    @property
    def manager(self) -> MemoryManager:
        return self._manager or get_memory_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="store_user_memory",
            description="Stores a fact, preference, task, commitment, or detail about the user in Aurora's long-term memory.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The exact fact, preference, or detail to remember (e.g. 'I prefer morning meetings between 9 and 10 AM')."
                    },
                    "type": {
                        "type": "string",
                        "description": "Optional category of memory.",
                        "enum": ["explicit", "preference", "fact", "commitment", "task", "episodic"],
                        "default": "explicit"
                    },
                    "importance": {
                        "type": "integer",
                        "description": "Importance level from 1 (trivial) to 5 (critical core truth). Defaults to 3.",
                        "minimum": 1,
                        "maximum": 5,
                        "default": 3
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional categorization tags (e.g. ['meetings', 'schedule'])."
                    }
                },
                "required": ["content"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        content = params.get("content", "").strip()
        if not content:
            return {"status": "error", "error": "Parameter 'content' is required and cannot be empty."}

        raw_type = params.get("type", "explicit")
        try:
            mem_type = MemoryType(raw_type)
        except ValueError:
            mem_type = MemoryType.EXPLICIT

        importance = params.get("importance", 3)
        tags = params.get("tags", [])

        memory, is_created = await self.manager.create_memory(
            content=content,
            type=mem_type,
            importance=importance,
            tags=tags
        )

        action_word = "Stored new memory" if is_created else "Updated existing memory"
        return {
            "status": "success",
            "memory_id": memory.id,
            "action": "created" if is_created else "updated",
            "type": memory.type.value,
            "content": memory.content,
            "tags": memory.tags,
            "importance": memory.importance,
            "message": f"{action_word}: '{memory.content}'"
        }


class RecallUserMemoryTool(BaseTool):
    """
    Tool allowing Aurora to actively search and recall facts, preferences,
    and notes from long-term memory relevant to a user question.
    """

    def __init__(self, memory_manager: Optional[MemoryManager] = None):
        self._manager = memory_manager

    @property
    def manager(self) -> MemoryManager:
        return self._manager or get_memory_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="recall_user_memory",
            description="Searches and recalls stored facts, preferences, commitments, or past details about the user.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search keywords or topic to recall (e.g. 'coffee preference', 'sister name', 'meeting time')."
                    },
                    "type": {
                        "type": "string",
                        "description": "Optional category filter.",
                        "enum": ["explicit", "preference", "fact", "commitment", "task", "episodic"]
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return (defaults to 5).",
                        "default": 5
                    }
                },
                "required": ["query"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = params.get("query", "").strip()
        if not query:
            return {"status": "error", "error": "Parameter 'query' is required."}

        raw_type = params.get("type")
        types_filter = None
        if raw_type:
            try:
                types_filter = [MemoryType(raw_type)]
            except ValueError:
                pass

        limit = min(10, max(1, params.get("limit", 5)))
        user_id = params.get("_user_id") or params.get("user_id")
        if user_id and str(user_id).strip():
            results = await self.manager.retrieve_memories(
                user_id=str(user_id).strip(),
                query=query,
                limit=limit,
                types=types_filter
            )
        else:
            results = await self.manager.retrieve_memories_unscoped_internal(
                query=query,
                limit=limit,
                types=types_filter
            )

        formatted = [
            {
                "id": m.id,
                "type": m.type.value,
                "content": m.content,
                "tags": m.tags,
                "importance": m.importance,
            }
            for m in results
        ]

        return {
            "status": "success",
            "query": query,
            "count": len(formatted),
            "memories": formatted
        }


class ForgetUserMemoryTool(BaseTool):
    """
    Tool allowing Aurora to archive/forget user memories.
    Requires explicit human confirmation before executing any deletion.
    """

    def __init__(self, memory_manager: Optional[MemoryManager] = None):
        self._manager = memory_manager

    @property
    def manager(self) -> MemoryManager:
        return self._manager or get_memory_manager()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="forget_user_memory",
            description="Archives or forgets specific stored memories. Requires user confirmation before execution.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword search to locate memories to forget (e.g. 'old address', 'dietary restriction')."
                    },
                    "memory_id": {
                        "type": "string",
                        "description": "Exact ID of the memory to forget, if already known."
                    },
                    "target_memory_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Internal bound IDs confirmed for deletion."
                    }
                }
            },
            risk_level=ToolRiskLevel.MEDIUM,
            requires_confirmation=True,
            required_permissions=["memory:delete"]
        )

    def prepare_confirmation(self, tool_call: ToolCall) -> PendingConfirmation:
        """
        Locates candidate memories, binds the exact IDs into arguments,
        and generates a transparent confirmation description for the user.
        """
        args = dict(tool_call.arguments or {})
        query = args.get("query", "").strip()
        memory_id = args.get("memory_id", "").strip()

        candidates = self.manager.store.search_candidates_sync(
            query=query if query else None,
            memory_id=memory_id if memory_id else None,
            limit=5
        )

        target_ids = [m.id for m in candidates]
        args["target_memory_ids"] = target_ids

        if candidates:
            summaries = [f"'{m.content}' (ID: {m.id})" for m in candidates]
            desc = f"Archive {len(candidates)} memory item(s): " + "; ".join(summaries)
        elif memory_id:
            desc = f"Archive memory with ID '{memory_id}' (not currently found in active store)."
        elif query:
            desc = f"No active memories found matching query '{query}'."
        else:
            desc = "No query or memory_id specified for deletion."

        bound_tool_call = ToolCall(
            id=tool_call.id,
            name=tool_call.name,
            arguments=args,
            metadata=dict(tool_call.metadata or {})
        )

        return PendingConfirmation(
            tool_call=bound_tool_call,
            risk_level=ToolRiskLevel.MEDIUM,
            description=desc
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes deletion ONLY on the pre-confirmed target_memory_ids.
        """
        target_ids = params.get("target_memory_ids", [])
        if not target_ids:
            # Fallback to single memory_id if explicitly approved directly
            single_id = params.get("memory_id")
            if single_id:
                target_ids = [single_id]

        if not target_ids:
            return {
                "status": "no_op",
                "deleted_count": 0,
                "message": "No matching active memories were targeted for deletion."
            }

        deleted_ids = []
        for mem_id in target_ids:
            success = await self.manager.delete_memory(mem_id, soft=True)
            if success:
                deleted_ids.append(mem_id)

        return {
            "status": "success",
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "message": f"Successfully archived {len(deleted_ids)} memory item(s)."
        }
