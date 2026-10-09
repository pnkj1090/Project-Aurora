import logging
from typing import Optional, List, Dict, Any, Union
from fastapi import APIRouter, HTTPException, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from app.services.ai.base import (
    AIAuthenticationError,
    AIRateLimitError,
    AITimeoutError,
    AIUnavailableError,
    AIProviderError,
)
from app.services.conversation import ConversationService, get_conversation_service
from app.services.conversation.service import ConversationIdentityError
from app.services.conversation.models import ActiveExecutionConflictError
from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.tools.registry import default_tool_registry
from app.services.web.citations.models import CitationOut

logger = logging.getLogger("aurora.api.chat")
router = APIRouter()


# MARK: - Schemas

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(..., min_length=1, max_length=10000, description="User message content.")
    conversation_id: Optional[str] = Field(None, description="Optional existing session ID. If omitted, a new conversation is created.")
    model: Optional[str] = Field(None, description="Optional override model name.")


class ConfirmActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(..., description="Conversation session ID.")
    confirmation_id: str = Field(..., description="The ID of the pending confirmation.")
    execution_id: str = Field(..., min_length=1, description="The active agent execution awaiting confirmation.")
    approved: bool = Field(..., description="True to authorize and execute the action, False to reject.")
    model: Optional[str] = Field(None, description="Optional override model name.")


class MessageOut(BaseModel):
    role: str
    content: str


class ToolConfirmationDetails(BaseModel):
    name: str
    arguments: Dict[str, Any]
    risk_level: str
    description: str


class ClientActionOut(BaseModel):
    action_id: str
    action: str
    updated_at: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    user_id: Optional[str] = None
    created_at: Optional[str] = None

    # Reminder fields (Phase 6B)
    reminder_id: Optional[str] = None
    notification_id: Optional[str] = None
    scheduled_at: Optional[str] = None
    body: Optional[str] = None

    # Common / Task / Commitment fields (Phase 6C)
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    is_active: Optional[bool] = None
    tags: Optional[List[str]] = None

    # Task specific
    task_id: Optional[str] = None
    due_at: Optional[str] = None
    completed_at: Optional[str] = None
    source_commitment_id: Optional[str] = None

    # Commitment specific
    commitment_id: Optional[str] = None
    target_date: Optional[str] = None


class ChatResponse(BaseModel):
    type: str = "message"  # "message" or "confirmation_required"
    conversation_id: str
    message: Optional[MessageOut] = None
    confirmation_id: Optional[str] = None
    tool: Optional[ToolConfirmationDetails] = None
    provider: str
    model: str
    execution_id: Optional[str] = None
    steps_count: Optional[int] = None
    step_activities: Optional[List[str]] = None
    # Phase 5K.4: Execution-scoped citations, constructed exclusively from CitationRegistry.
    # Populated only when the final assistant response references validated web sources.
    # Never contains model-generated metadata. Never persisted.
    citations: Optional[List[CitationOut]] = None
    # Phase 6B: Ephemeral assistant notification actions for client-side local notification scheduling
    client_actions: Optional[List[ClientActionOut]] = None


class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    messages: List[MessageOut]
    created_at: str
    updated_at: str


class ToolItemOut(BaseModel):
    name: str
    description: str
    risk_level: str
    requires_confirmation: bool
    parameters_schema: Dict[str, Any]


# MARK: - Endpoints

@router.post("/chat", response_model=ChatResponse)
async def post_chat(
    request: ChatRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service)
):
    """
    Send a message through the Conversation Engine and active AI Gateway.
    If the AI requests an action requiring user confirmation, execution pauses
    and returns a response with type 'confirmation_required'.
    """
    try:
        session, assistant_msg, ai_response, pending, citations_out = await service.process_chat(
            user_text=request.message,
            conversation_id=request.conversation_id,
            model=request.model,
            authenticated_user_id=principal.user.user_id,
        )

        execution_state = session.active_agent_execution
        exec_id = execution_state.execution_id if execution_state else None
        steps_count = execution_state.current_step if execution_state else None
        step_activities = execution_state.step_activities if execution_state else None

        if pending:
            return ChatResponse(
                type="confirmation_required",
                conversation_id=session.conversation_id,
                confirmation_id=pending.confirmation_id,
                tool=ToolConfirmationDetails(
                    name=pending.tool_call.name,
                    arguments=pending.tool_call.arguments,
                    risk_level=pending.risk_level.value,
                    description=pending.description
                ),
                provider=ai_response.provider,
                model=ai_response.model,
                execution_id=exec_id,
                steps_count=steps_count,
                step_activities=step_activities,
                # No citations on confirmation_required; execution not yet synthesized
            )

        client_actions = None
        if execution_state and execution_state.client_actions and not pending:
            client_actions = [ClientActionOut(**act) for act in execution_state.client_actions]

        return ChatResponse(
            type="message",
            conversation_id=session.conversation_id,
            message=MessageOut(
                role=assistant_msg.role.value,
                content=assistant_msg.content
            ),
            provider=ai_response.provider,
            model=ai_response.model,
            execution_id=exec_id,
            steps_count=steps_count,
            step_activities=step_activities,
            citations=citations_out if citations_out else None,
            client_actions=client_actions,
        )

    except ActiveExecutionConflictError as conf_err:
        logger.warning("Active execution conflict in post_chat: %s", str(conf_err))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(conf_err)
        )
    except ConversationIdentityError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation session not found.")
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err)
        )
    except AIAuthenticationError as auth_err:
        logger.error("AI Provider authentication failure: %s", str(auth_err))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="AI Provider authentication failed. Check backend credentials."
        )
    except AIRateLimitError as rate_err:
        logger.warning("AI Provider rate limit: %s", str(rate_err))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI Provider rate limit or quota exceeded. Please try again shortly."
        )
    except AITimeoutError as timeout_err:
        logger.warning("AI Provider timeout: %s", str(timeout_err))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="AI Provider request timed out."
        )
    except AIUnavailableError as unavail_err:
        logger.error("AI Provider unavailable: %s", str(unavail_err))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI Provider service is temporarily unavailable."
        )
    except AIProviderError as prov_err:
        logger.error("AI Provider error: %s", str(prov_err))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error returned by AI Provider."
        )
    except Exception as exc:
        logger.exception("Unexpected error in chat endpoint: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal server error occurred."
        )


@router.post("/chat/confirm", response_model=ChatResponse)
async def post_confirm_action(
    request: ConfirmActionRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service)
):
    """
    Approve or reject a pending tool action.
    Resumes the Conversation Engine to execute authorized actions or process the cancellation.
    """
    try:
        session, assistant_msg, ai_response, citations_out = await service.confirm_tool_action(
            conversation_id=request.conversation_id,
            confirmation_id=request.confirmation_id,
            approved=request.approved,
            model=request.model,
            authenticated_user_id=principal.user.user_id,
            execution_id=request.execution_id,
        )

        execution_state = session.active_agent_execution
        exec_id = execution_state.execution_id if execution_state else None
        steps_count = execution_state.current_step if execution_state else None
        step_activities = execution_state.step_activities if execution_state else None

        client_actions = None
        if execution_state and execution_state.client_actions:
            client_actions = [ClientActionOut(**act) for act in execution_state.client_actions]

        return ChatResponse(
            type="message",
            conversation_id=session.conversation_id,
            message=MessageOut(
                role=assistant_msg.role.value,
                content=assistant_msg.content
            ),
            provider=ai_response.provider,
            model=ai_response.model,
            execution_id=exec_id,
            steps_count=steps_count,
            step_activities=step_activities,
            citations=citations_out if citations_out else None,
            client_actions=client_actions,
        )

    except ActiveExecutionConflictError as conf_err:
        logger.warning("Active execution conflict in post_confirm_action: %s", str(conf_err))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(conf_err)
        )
    except ConversationIdentityError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending confirmation not found.")
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err)
        )
    except Exception as exc:
        logger.exception("Unexpected error in confirm action: %s", str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal server error occurred during action confirmation."
        )


@router.get("/conversations/{conversation_id}", response_model=ConversationHistoryResponse)
async def get_conversation_history(
    conversation_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ConversationService = Depends(get_conversation_service)
):
    """
    Retrieve message history for a given conversation session.
    """
    session = await service.get_session(conversation_id)
    if not session or session.user_id != principal.user.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation session '{conversation_id}' not found."
        )

    return ConversationHistoryResponse(
        conversation_id=session.conversation_id,
        messages=[
            MessageOut(role=m.role.value, content=m.content)
            for m in session.messages
        ],
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


@router.get("/tools", response_model=List[ToolItemOut])
async def list_available_tools():
    """
    Returns definitions of all registered tools and their input schemas.
    """
    definitions = default_tool_registry.get_definitions()
    return [
        ToolItemOut(
            name=d.name,
            description=d.description,
            risk_level=d.risk_level.value,
            requires_confirmation=d.requires_confirmation,
            parameters_schema=d.parameters_schema
        )
        for d in definitions
    ]
