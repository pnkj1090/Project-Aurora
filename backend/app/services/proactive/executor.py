"""
Phase 5I — ProactiveActionExecutor.

Governs safe proposal resolution and action execution for active proactive candidates:
- Runtime permission check (TOCTOU prevention).
- Candidate lifecycle transitions: ELIGIBLE -> ACCEPTED -> EXECUTED (or FAILED).
- OPEN_* navigation actions: constructs safe typed NavigationTarget(type, id).
- Low-risk execution: COMPLETE_TASK, COMPLETE_COMMITMENT via ToolExecutor.
- Confirmation-required execution: CANCEL_REMINDER via Phase 3 ToolExecutor & PendingConfirmation.
- Strictly rejects NOTIFY (non-executable in Phase 5I).
- Sanitizes all public responses; zero internal field leaks.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.permissions.models import PermissionType
from app.services.permissions.manager import PermissionManager, default_permission_manager
from app.services.tasks import TaskManager, get_task_manager
from app.services.commitments import CommitmentManager, get_commitment_manager
from app.services.calendar import CalendarManager, get_calendar_manager
from app.services.reminders import ReminderManager, get_reminder_manager
from app.services.tools.executor import ToolExecutor, ToolConfirmationRequired
from app.services.tools.schemas import ToolCall
from app.services.conversation import ConversationService, get_conversation_service
from app.services.conversation.session import ConversationSession
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveAction,
    ProactiveStatus,
)
from app.services.proactive.store import BaseProactiveStore, InMemoryProactiveStore
from app.services.proactive.schemas import (
    NavigationTarget,
    NavigationTargetType,
    ProactivePendingConfirmationOut,
    ProactiveResolutionResponse,
)

logger = logging.getLogger("aurora.proactive.executor")


class ProactiveActionExecutor:
    """
    Dedicated executor validating permissions, confirmation requirements,
    and dispatching approved proactive actions.
    """

    def __init__(
        self,
        store: Optional[BaseProactiveStore] = None,
        task_manager: Optional[TaskManager] = None,
        commitment_manager: Optional[CommitmentManager] = None,
        calendar_manager: Optional[CalendarManager] = None,
        reminder_manager: Optional[ReminderManager] = None,
        permission_manager: Optional[PermissionManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        conversation_service: Optional[ConversationService] = None,
    ):
        from app.services.proactive.manager import get_proactive_manager
        self.store = store or get_proactive_manager().store
        self._task_manager = task_manager
        self._commitment_manager = commitment_manager
        self._calendar_manager = calendar_manager
        self._reminder_manager = reminder_manager
        self._permission_manager = permission_manager or default_permission_manager
        self._tool_executor = tool_executor or ToolExecutor(permission_manager=self._permission_manager)
        self._conversation_service = conversation_service

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager or get_task_manager()

    @property
    def commitment_manager(self) -> CommitmentManager:
        return self._commitment_manager or get_commitment_manager()

    @property
    def calendar_manager(self) -> CalendarManager:
        return self._calendar_manager or get_calendar_manager()

    @property
    def reminder_manager(self) -> ReminderManager:
        return self._reminder_manager or get_reminder_manager()

    @property
    def conversation_service(self) -> ConversationService:
        if self._conversation_service is None:
            self._conversation_service = get_conversation_service()
        return self._conversation_service

    async def _pause_for_confirmation(
        self,
        candidate: ProactiveCandidate,
        tool_call: ToolCall,
        pending,
        user_id: str,
        conversation_id: Optional[str],
        message: str,
    ) -> ProactiveResolutionResponse:
        """Attach a proactive proposal to the existing server-held confirmation flow."""
        from app.services.conversation.models import AgentExecutionState, AgentExecutionStatus

        pending.proactive_candidate_id = candidate.candidate_id
        conv_id = conversation_id or str(uuid.uuid4())
        session = await self.conversation_service.store.get(conv_id)
        if session is None:
            session = ConversationSession(conversation_id=conv_id, metadata={"user_id": user_id})
        elif session.user_id != user_id:
            raise ValueError("Conversation session not found.")
        elif session.has_active_execution() or session.pending_confirmation is not None:
            raise ValueError("Conversation already has an active execution.")

        execution_state = AgentExecutionState(
            conversation_id=conv_id,
            user_id=user_id,
            execution_status=AgentExecutionStatus.WAITING_FOR_CONFIRMATION,
            paused_tool_call=tool_call.model_copy(deep=True),
            pending_confirmation_id=pending.confirmation_id,
        )
        pending.agent_execution_id = execution_state.execution_id
        session.active_agent_execution = execution_state
        session.pending_confirmation = pending
        candidate.pending_confirmation_id = pending.confirmation_id
        await self.conversation_service.store.save(session)
        await self.store.update(candidate)

        return ProactiveResolutionResponse(
            candidate_id=candidate.candidate_id,
            status="accepted",
            confirmation_required=True,
            conversation_id=session.conversation_id,
            execution_id=execution_state.execution_id,
            pending_confirmation=ProactivePendingConfirmationOut(
                confirmation_id=pending.confirmation_id,
                tool_name=tool_call.name,
                risk_level=pending.risk_level.value,
                description=pending.description,
            ),
            message=message,
        )

    async def _execute_or_pause(
        self,
        candidate: ProactiveCandidate,
        tool_call: ToolCall,
        user_id: str,
        conversation_id: Optional[str],
        confirmation_message: str,
    ):
        """Run through ToolExecutor so permission checks precede confirmation handling."""
        try:
            result = await self._tool_executor.execute(tool_call, is_pre_approved=False, user_id=user_id)
        except ToolConfirmationRequired as required:
            try:
                return await self._pause_for_confirmation(
                    candidate, tool_call, required.pending, user_id,
                    conversation_id, confirmation_message,
                )
            except Exception as exc:
                logger.exception("Failed to register confirmation for proactive candidate '%s'.", candidate.candidate_id)
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = "Failed to register confirmation request."
                candidate.internal_error = str(exc)
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )
        return result

    async def resolve_candidate_action(
        self,
        candidate_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        conversation_id: Optional[str] = None,
    ) -> ProactiveResolutionResponse:
        """
        Main entry point for resolving / accepting a proactive candidate.
        Enforces user ownership, lifecycle validation, TOCTOU permission checks,
        and safe execution.
        """
        # 1. Fetch candidate strictly scoped to user_id
        candidate = await self.store.get(candidate_id, user_id=user_id)
        if candidate is None:
            return ProactiveResolutionResponse(
                candidate_id=candidate_id,
                status="failed",
                error_message="Candidate proposal not found.",
            )

        # 2. Check lifecycle eligibility
        if candidate.is_terminal or candidate.status not in (
            ProactiveStatus.ELIGIBLE,
            ProactiveStatus.DELIVERED,
        ):
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="failed",
                error_message=f"Candidate with status '{candidate.status.value}' cannot be accepted.",
            )

        # 3. Check expiration
        if candidate.is_expired:
            candidate.status = ProactiveStatus.EXPIRED
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="failed",
                error_message="Candidate has expired.",
            )

        # 4. Check for strictly non-executable actions (NOTIFY)
        if candidate.action == ProactiveAction.NOTIFY:
            candidate.status = ProactiveStatus.FAILED
            candidate.safe_error_message = "Action 'notify' is not executable in Phase 5I."
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="failed",
                error_message=candidate.safe_error_message,
            )

        # 5. Runtime Permission TOCTOU check
        if candidate.action == ProactiveAction.OPEN_CALENDAR_EVENT:
            is_auth = await self._permission_manager.is_authorized(PermissionType.CALENDAR, user_id=user_id)
            if not is_auth:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = "Action blocked: Required permission 'calendar' is not authorized."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

        elif candidate.action in (ProactiveAction.OPEN_REMINDER, ProactiveAction.CANCEL_REMINDER):
            is_auth = await self._permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)
            if not is_auth:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = "Action blocked: Required permission 'reminders' is not authorized."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

        # 6. Candidate transitions to ACCEPTED
        # ACCEPTED = user accepted proposal, execution resolution has begun (not yet executed).
        candidate.status = ProactiveStatus.ACCEPTED
        await self.store.update(candidate)

        # 7. Action Execution Handling
        action = candidate.action

        # --- A. OPEN_TASK ---
        if action == ProactiveAction.OPEN_TASK:
            task_id = str(candidate.action_payload.get("task_id") or candidate.reference_id or "")
            task = await self.task_manager.get_task(task_id, user_id=user_id) if task_id else None
            if task is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Task '{task_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            candidate.status = ProactiveStatus.EXECUTED
            candidate.executed_at = datetime.now(timezone.utc)
            candidate.safe_message = "Task details opened."
            candidate.navigation_target_type = "task"
            candidate.navigation_target_id = task.id
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="executed",
                message=candidate.safe_message,
                navigation_target=NavigationTarget(type=NavigationTargetType.TASK, id=task.id),
            )

        # --- B. OPEN_COMMITMENT ---
        elif action == ProactiveAction.OPEN_COMMITMENT:
            cmt_id = str(candidate.action_payload.get("commitment_id") or candidate.reference_id or "")
            cmt = await self.commitment_manager.get_commitment(cmt_id, user_id=user_id) if cmt_id else None
            if cmt is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Commitment '{cmt_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            candidate.status = ProactiveStatus.EXECUTED
            candidate.executed_at = datetime.now(timezone.utc)
            candidate.safe_message = "Commitment details opened."
            candidate.navigation_target_type = "commitment"
            candidate.navigation_target_id = cmt.id
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="executed",
                message=candidate.safe_message,
                navigation_target=NavigationTarget(type=NavigationTargetType.COMMITMENT, id=cmt.id),
            )

        # --- C. OPEN_CALENDAR_EVENT ---
        elif action == ProactiveAction.OPEN_CALENDAR_EVENT:
            event_id = str(candidate.action_payload.get("event_id") or candidate.reference_id or "")
            now_utc = datetime.now(timezone.utc)
            events, _ = await self.calendar_manager.get_events(
                start_at=now_utc - timedelta(days=1),
                end_at=now_utc + timedelta(days=28),
                max_results=50,
                user_id=user_id,
            )
            matching_event = next((e for e in (events or []) if e.event_id == event_id), None)
            if matching_event is None:
                user_events = getattr(self.calendar_manager, "_events", {}).get(user_id, [])
                matching_event = next((e for e in user_events if e.event_id == event_id), None)

            if matching_event is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Calendar event '{event_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            candidate.status = ProactiveStatus.EXECUTED
            candidate.executed_at = datetime.now(timezone.utc)
            candidate.safe_message = "Calendar event opened."
            candidate.navigation_target_type = "calendar_event"
            candidate.navigation_target_id = matching_event.event_id
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="executed",
                message=candidate.safe_message,
                navigation_target=NavigationTarget(type=NavigationTargetType.CALENDAR_EVENT, id=matching_event.event_id),
            )

        # --- D. OPEN_REMINDER ---
        elif action == ProactiveAction.OPEN_REMINDER:
            reminder_id = str(candidate.action_payload.get("reminder_id") or candidate.reference_id or "")
            reminder = await self.reminder_manager.get_reminder(reminder_id, user_id=user_id) if reminder_id else None
            if reminder is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Reminder '{reminder_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            candidate.status = ProactiveStatus.EXECUTED
            candidate.executed_at = datetime.now(timezone.utc)
            candidate.safe_message = "Reminder details opened."
            candidate.navigation_target_type = "reminder"
            candidate.navigation_target_id = reminder.id
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="executed",
                message=candidate.safe_message,
                navigation_target=NavigationTarget(type=NavigationTargetType.REMINDER, id=reminder.id),
            )

        # --- E. COMPLETE_TASK ---
        elif action == ProactiveAction.COMPLETE_TASK:
            task_id = str(candidate.action_payload.get("task_id") or candidate.reference_id or "")
            # Validate task ownership first
            task = await self.task_manager.get_task(task_id, user_id=user_id) if task_id else None
            if task is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Task '{task_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            tool_call = ToolCall(name="complete_task", arguments={"task_id": task_id})
            result = await self._execute_or_pause(
                candidate, tool_call, user_id, conversation_id,
                "Confirmation required to complete this task.",
            )
            if isinstance(result, ProactiveResolutionResponse):
                return result

            if result.success:
                candidate.status = ProactiveStatus.EXECUTED
                candidate.executed_at = datetime.now(timezone.utc)
                candidate.safe_message = "Task completed successfully."
                candidate.internal_execution_result = result.output if isinstance(result.output, dict) else {"output": result.output}
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="executed",
                    message=candidate.safe_message,
                )
            else:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = "Task completion failed."
                candidate.internal_error = result.error
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

        # --- F. COMPLETE_COMMITMENT ---
        elif action == ProactiveAction.COMPLETE_COMMITMENT:
            cmt_id = str(candidate.action_payload.get("commitment_id") or candidate.reference_id or "")
            cmt = await self.commitment_manager.get_commitment(cmt_id, user_id=user_id) if cmt_id else None
            if cmt is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Commitment '{cmt_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            tool_call = ToolCall(name="complete_commitment", arguments={"commitment_id": cmt_id})
            result = await self._execute_or_pause(
                candidate, tool_call, user_id, conversation_id,
                "Confirmation required to complete this commitment.",
            )
            if isinstance(result, ProactiveResolutionResponse):
                return result

            if result.success:
                candidate.status = ProactiveStatus.EXECUTED
                candidate.executed_at = datetime.now(timezone.utc)
                candidate.safe_message = "Commitment fulfilled successfully."
                candidate.internal_execution_result = result.output if isinstance(result.output, dict) else {"output": result.output}
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="executed",
                    message=candidate.safe_message,
                )
            else:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = "Commitment completion failed."
                candidate.internal_error = result.error
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

        # --- G. CANCEL_REMINDER (Confirmation Required) ---
        elif action == ProactiveAction.CANCEL_REMINDER:
            reminder_id = str(candidate.action_payload.get("reminder_id") or candidate.reference_id or "")
            reminder = await self.reminder_manager.get_reminder(reminder_id, user_id=user_id) if reminder_id else None
            if reminder is None:
                candidate.status = ProactiveStatus.FAILED
                candidate.safe_error_message = f"Reminder '{reminder_id}' not found."
                await self.store.update(candidate)
                return ProactiveResolutionResponse(
                    candidate_id=candidate.candidate_id,
                    status="failed",
                    error_message=candidate.safe_error_message,
                )

            tool_call = ToolCall(name="cancel_reminder", arguments={"reminder_id": reminder_id})
            pending = self._tool_executor.check_confirmation_requirement(tool_call)

            if pending is not None:
                try:
                    return await self._pause_for_confirmation(
                        candidate, tool_call, pending, user_id, conversation_id,
                        "Confirmation required to cancel reminder.",
                    )
                except Exception as exc:
                    logger.exception("Failed to register pending confirmation for candidate '%s': %s", candidate.candidate_id, exc)
                    candidate.status = ProactiveStatus.FAILED
                    candidate.safe_error_message = "Failed to register confirmation request."
                    candidate.internal_error = str(exc)
                    await self.store.update(candidate)
                    return ProactiveResolutionResponse(
                        candidate_id=candidate.candidate_id,
                        status="failed",
                        error_message=candidate.safe_error_message,
                    )
            else:
                # Tool did not require confirmation; execute immediately
                result = await self._tool_executor.execute(tool_call, is_pre_approved=False, user_id=user_id)
                if result.success:
                    candidate.status = ProactiveStatus.EXECUTED
                    candidate.executed_at = datetime.now(timezone.utc)
                    candidate.safe_message = "Reminder cancelled successfully."
                    candidate.internal_execution_result = result.output if isinstance(result.output, dict) else {"output": result.output}
                    await self.store.update(candidate)
                    return ProactiveResolutionResponse(
                        candidate_id=candidate.candidate_id,
                        status="executed",
                        message=candidate.safe_message,
                    )
                else:
                    candidate.status = ProactiveStatus.FAILED
                    candidate.safe_error_message = "Reminder cancellation failed."
                    candidate.internal_error = result.error
                    await self.store.update(candidate)
                    return ProactiveResolutionResponse(
                        candidate_id=candidate.candidate_id,
                        status="failed",
                        error_message=candidate.safe_error_message,
                    )

        else:
            candidate.status = ProactiveStatus.FAILED
            candidate.safe_error_message = f"Unsupported proactive action '{action.value}'."
            await self.store.update(candidate)
            return ProactiveResolutionResponse(
                candidate_id=candidate.candidate_id,
                status="failed",
                error_message=candidate.safe_error_message,
            )


# Singleton instance
_default_proactive_action_executor: Optional[ProactiveActionExecutor] = None


def get_proactive_action_executor() -> ProactiveActionExecutor:
    """Retrieve or create the singleton ProactiveActionExecutor."""
    global _default_proactive_action_executor
    if _default_proactive_action_executor is None:
        _default_proactive_action_executor = ProactiveActionExecutor()
    return _default_proactive_action_executor
