from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.calendar.manager import CalendarManager, get_calendar_manager
from app.services.calendar.models import CalendarEvent
from app.services.commitments import CommitmentManager, get_commitment_manager
from app.services.commitments.models import Commitment
from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.memory import MemoryManager, get_memory_manager
from app.services.memory.schemas import MemorySource, MemoryStatus, MemoryType
from app.services.permissions import PermissionManager, get_permission_manager
from app.services.permissions.models import PermissionType
from app.services.reminders import ReminderManager, get_reminder_manager
from app.services.tasks import TaskManager, get_task_manager
from app.services.tasks.models import Task, TaskPriority

from app.services.agenda.briefing_models import (
    DEBRIEF_PROPOSAL_TTL_MINUTES,
    DEBRIEF_PROPOSAL_TTL_SECONDS,
    DebriefFailureReason,
    DebriefOperation,
    DebriefOperationPrecondition,
    DebriefOperationStatus,
    DebriefOperationType,
    DebriefProposal,
    DebriefProposalStatus,
    DebriefStateTransitionError,
    EventBriefing,
    MemoryCreateExecutionPrecondition,
    ReminderCreateExecutionPrecondition,
    ReminderLinkType,
    TaskCreateExecutionPrecondition,
    compute_debrief_state_hash,
    serialize_canonical_state,
    validate_debrief_state_transition,
)
from app.services.agenda.coordination import resolve_task_duration

logger = logging.getLogger("aurora.agenda.briefing")


class EventBriefingService:
    """
    Authoritative service governing Event Briefing and Debrief Orchestration (Phase 6F).
    - Read-only event briefing synthesis (`generate_briefing`).
    - Server-held debrief proposal creation (`propose_debrief`).
    - Two-tier governed debrief proposal execution (`execute_proposal`).
    - Strict DebriefProposalStatus state machine.
    """

    def __init__(
        self,
        calendar_manager: Optional[CalendarManager] = None,
        task_manager: Optional[TaskManager] = None,
        commitment_manager: Optional[CommitmentManager] = None,
        reminder_manager: Optional[ReminderManager] = None,
        memory_manager: Optional[MemoryManager] = None,
        permission_manager: Optional[PermissionManager] = None,
    ):
        self._calendar_manager = calendar_manager
        self._task_manager = task_manager
        self._commitment_manager = commitment_manager
        self._reminder_manager = reminder_manager
        self._memory_manager = memory_manager
        self._permission_manager = permission_manager

        # In-memory proposal store keyed by proposal_id
        self._proposals: Dict[str, DebriefProposal] = {}

    @property
    def calendar_manager(self) -> CalendarManager:
        return self._calendar_manager or get_calendar_manager()

    @property
    def task_manager(self) -> TaskManager:
        return self._task_manager or get_task_manager()

    @property
    def commitment_manager(self) -> CommitmentManager:
        return self._commitment_manager or get_commitment_manager()

    @property
    def reminder_manager(self) -> ReminderManager:
        return self._reminder_manager or get_reminder_manager()

    @property
    def memory_manager(self) -> MemoryManager:
        return self._memory_manager or get_memory_manager()

    @property
    def permission_manager(self) -> PermissionManager:
        return self._permission_manager or get_permission_manager()

    def _find_calendar_event(self, event_id: str, user_id: str) -> Optional[CalendarEvent]:
        """Helper to locate a calendar event for active user in calendar manager snapshot."""
        user_events = self.calendar_manager._events.get(user_id, [])
        for ev in user_events:
            if ev.event_id == event_id:
                return ev.model_copy(deep=True)
        return None

    # ── 1. Event Briefing Synthesis (Read-Only) ───────────────────────────────

    async def generate_briefing(
        self,
        event_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> EventBriefing:
        """
        Synthesizes calendar interval, attendees, related tasks, active commitments,
        and relevant episodic memories into a structured read-only EventBriefing.
        """
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required.")

        event = self._find_calendar_event(event_id=event_id, user_id=user_id)
        if event is None:
            raise ValueError(f"Calendar event '{event_id}' not found for user '{user_id}'.")

        # 1. Attendees (CalendarEvent minimizes attendee data for privacy)
        raw_attendees = getattr(event, "attendees", [])
        attendees = [
            {"email": getattr(att, "email", str(att)), "name": getattr(att, "name", str(att)), "status": getattr(att, "status", "accepted")}
            if not isinstance(att, dict) else att
            for att in raw_attendees
        ]

        # 2. Correlated Tasks: search tasks matching event title keywords
        all_tasks = await self.task_manager.list_tasks(user_id=user_id, limit=100)
        keywords = set(w.lower() for w in event.title.split() if len(w) > 3)
        related_tasks: List[Dict[str, Any]] = []
        for t in all_tasks:
            t_title_lower = t.title.lower()
            if any(kw in t_title_lower for kw in keywords):
                related_tasks.append({
                    "task_id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "due_at": t.due_at.isoformat() if t.due_at else None,
                    "priority": t.priority.value,
                })

        # 3. Active Commitments
        all_commitments = await self.commitment_manager.list_commitments(user_id=user_id, limit=100)
        active_commitments: List[Dict[str, Any]] = []
        for c in all_commitments:
            c_title_lower = c.title.lower()
            if any(kw in c_title_lower for kw in keywords):
                active_commitments.append({
                    "commitment_id": c.id,
                    "title": c.title,
                    "status": c.status.value,
                    "target_date": c.target_date.strftime("%Y-%m-%d") if c.target_date else None,
                })

        # 4. Relevant Memories (Lexical retrieval)
        relevant_memories: List[Dict[str, Any]] = []
        try:
            mems = await self.memory_manager.retriever.retrieve(
                query=event.title,
                limit=5,
                user_id=user_id,
            )
            for m in mems:
                relevant_memories.append({
                    "memory_id": m.id,
                    "content": m.content,
                    "type": m.type.value,
                })
        except Exception as exc:
            logger.warning("Failed to retrieve memories for briefing: %s", exc)

        summary = (
            f"Briefing for '{event.title}': {len(attendees)} attendees, "
            f"{len(related_tasks)} related tasks, {len(active_commitments)} active commitments, "
            f"{len(relevant_memories)} contextual memories."
        )

        return EventBriefing(
            event_id=event.event_id,
            user_id=user_id,
            title=event.title,
            start_time=event.start_at,
            end_time=event.end_at,
            location=event.location,
            attendees=attendees,
            related_tasks=related_tasks,
            active_commitments=active_commitments,
            relevant_memories=relevant_memories,
            summary=summary,
        )

    # ── 2. Debrief Proposal Generation (Read-Only) ────────────────────────────

    async def propose_debrief(
        self,
        event_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        raw_notes: str = "",
        task_candidates: Optional[List[Dict[str, Any]]] = None,
        reminder_candidates: Optional[List[Dict[str, Any]]] = None,
        memory_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> DebriefProposal:
        """
        Synthesizes post-event debrief proposal with strict preconditions and canonical state hash.
        Stores proposal in server cache with status DRAFT.
        """
        if not user_id or not user_id.strip():
            raise ValueError("user_id is required.")

        event = self._find_calendar_event(event_id=event_id, user_id=user_id)
        if event is None:
            raise ValueError(f"Calendar event '{event_id}' not found for user '{user_id}'.")

        ordered_ops: List[DebriefOperation] = []
        referenced_task_ids: set[str] = set()
        referenced_commitment_ids: set[str] = set()

        # 1. Process Task Candidates
        for tc in (task_candidates or []):
            raw_title = tc.get("title") or tc.get("exact_title")
            if not raw_title or not str(raw_title).strip():
                raise ValueError("Task candidate requires a non-empty 'exact_title'.")

            exact_title = str(raw_title).strip()
            exact_due_at = tc.get("due_at") or tc.get("exact_due_at")
            if exact_due_at is not None and isinstance(exact_due_at, str):
                exact_due_at = datetime.fromisoformat(exact_due_at)

            # Phase 6E Duration Contract:
            raw_dur = tc.get("duration_minutes") if "duration_minutes" in tc else tc.get("exact_duration_minutes")
            if raw_dur is not None:
                if not isinstance(raw_dur, int) or isinstance(raw_dur, bool) or raw_dur < 15 or raw_dur > 1440:
                    raise ValueError(f"Task duration must be integer >= 15 and <= 1440, got {raw_dur!r}.")
                exact_duration_minutes = raw_dur
            else:
                # Absent duration: resolve via existing Phase 6E default duration (30 mins)
                exact_duration_minutes = 30

            raw_priority = tc.get("priority") or tc.get("exact_priority") or TaskPriority.MEDIUM
            exact_priority = TaskPriority(raw_priority) if isinstance(raw_priority, str) else raw_priority
            exact_commitment_id = tc.get("commitment_id") or tc.get("exact_commitment_id")
            if exact_commitment_id:
                referenced_commitment_ids.add(exact_commitment_id)

            precond = TaskCreateExecutionPrecondition(
                exact_title=exact_title,
                exact_due_at=exact_due_at,
                exact_duration_minutes=exact_duration_minutes,
                exact_priority=exact_priority,
                exact_commitment_id=exact_commitment_id,
            )
            op = DebriefOperation(
                operation_type=DebriefOperationType.CREATE_TASK,
                precondition=DebriefOperationPrecondition(
                    operation_type=DebriefOperationType.CREATE_TASK,
                    task_create=precond,
                ),
                proposed_payload={
                    "exact_title": exact_title,
                    "exact_due_at": exact_due_at.isoformat() if exact_due_at else None,
                    "exact_duration_minutes": exact_duration_minutes,
                    "exact_priority": exact_priority.value,
                    "exact_commitment_id": exact_commitment_id,
                    "title": exact_title,
                    "due_at": exact_due_at.isoformat() if exact_due_at else None,
                    "duration_minutes": exact_duration_minutes,
                    "priority": exact_priority.value,
                    "commitment_id": exact_commitment_id,
                },
            )
            ordered_ops.append(op)

        # 2. Process Reminder Candidates
        for rc in (reminder_candidates or []):
            raw_title = rc.get("exact_title") or rc.get("title")
            if not raw_title or not str(raw_title).strip():
                raise ValueError("Reminder candidate requires a non-empty 'exact_title'.")

            exact_title = str(raw_title).strip()
            raw_sched = rc.get("exact_scheduled_at") or rc.get("scheduled_at")
            if not raw_sched:
                raise ValueError("Reminder candidate requires 'exact_scheduled_at'.")
            exact_sched = datetime.fromisoformat(raw_sched) if isinstance(raw_sched, str) else raw_sched

            raw_link_type = rc.get("linked_entity_type", ReminderLinkType.NONE)
            link_type = ReminderLinkType(raw_link_type)
            link_id = rc.get("linked_entity_id")

            if link_type == ReminderLinkType.TASK:
                if not link_id:
                    raise ValueError("linked_entity_id is required when linked_entity_type is TASK.")
                referenced_task_ids.add(link_id)
            elif link_type == ReminderLinkType.COMMITMENT:
                if not link_id:
                    raise ValueError("linked_entity_id is required when linked_entity_type is COMMITMENT.")
                referenced_commitment_ids.add(link_id)
            elif link_type == ReminderLinkType.NONE:
                link_id = None

            precond = ReminderCreateExecutionPrecondition(
                exact_title=exact_title,
                exact_scheduled_at=exact_sched,
                linked_entity_type=link_type,
                linked_entity_id=link_id,
            )
            op = DebriefOperation(
                operation_type=DebriefOperationType.CREATE_REMINDER,
                precondition=DebriefOperationPrecondition(
                    operation_type=DebriefOperationType.CREATE_REMINDER,
                    reminder_create=precond,
                ),
                proposed_payload={
                    "exact_title": exact_title,
                    "exact_scheduled_at": exact_sched.isoformat(),
                    "linked_entity_type": link_type.value,
                    "linked_entity_id": link_id,
                    "title": exact_title,
                    "scheduled_at": exact_sched.isoformat(),
                },
            )
            ordered_ops.append(op)

        # 3. Process Memory Candidates (or default from raw_notes)
        m_cands = memory_candidates or []
        if not m_cands and raw_notes.strip():
            m_cands = [{
                "exact_content": f"Notes from meeting '{event.title}': {raw_notes.strip()}",
                "context_tags": ["meeting", "debrief"],
            }]

        for mc in m_cands:
            raw_content = mc.get("exact_content") or mc.get("content")
            if not raw_content or not str(raw_content).strip():
                raise ValueError("Memory candidate requires non-empty 'exact_content'.")

            exact_content = str(raw_content).strip()
            raw_mem_type = mc.get("exact_memory_type") or mc.get("memory_type") or MemoryType.EPISODIC
            exact_mem_type = MemoryType(raw_mem_type) if isinstance(raw_mem_type, str) else raw_mem_type
            raw_conf = mc.get("exact_confidence") if "exact_confidence" in mc else mc.get("confidence", 1.0)
            exact_conf = float(raw_conf)
            context_tags = mc.get("context_tags", ["meeting", "debrief"])

            # Option A: exact_source is intentionally excluded to guarantee server-authoritative provenance
            precond = MemoryCreateExecutionPrecondition(
                exact_content=exact_content,
                exact_memory_type=exact_mem_type,
                exact_confidence=exact_conf,
                source_event_id=event_id,
                context_tags=context_tags,
            )
            op = DebriefOperation(
                operation_type=DebriefOperationType.CREATE_MEMORY,
                precondition=DebriefOperationPrecondition(
                    operation_type=DebriefOperationType.CREATE_MEMORY,
                    memory_create=precond,
                ),
                proposed_payload={
                    "exact_content": exact_content,
                    "exact_memory_type": exact_mem_type.value,
                    "exact_confidence": exact_conf,
                    "source_event_id": event_id,
                    "context_tags": context_tags,
                    "content": exact_content,
                    "memory_type": exact_mem_type.value,
                    "confidence": exact_conf,
                },
            )
            ordered_ops.append(op)

        # 4. Canonical State Hashing across referenced entities
        tasks_to_hash: List[Task] = []
        for tid in referenced_task_ids:
            t = await self.task_manager.get_task(task_id=tid, user_id=user_id)
            if t:
                tasks_to_hash.append(t)

        commitments_to_hash: List[Commitment] = []
        for cid in referenced_commitment_ids:
            c = await self.commitment_manager.get_commitment(commitment_id=cid, user_id=user_id)
            if c:
                commitments_to_hash.append(c)

        state_hash = compute_debrief_state_hash(tasks_to_hash, commitments_to_hash)

        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(minutes=DEBRIEF_PROPOSAL_TTL_MINUTES)

        proposal = DebriefProposal(
            event_id=event_id,
            user_id=user_id,
            created_at=now_utc,
            expires_at=expires_at,
            status=DebriefProposalStatus.DRAFT,
            state_hash=state_hash,
            ordered_operations=ordered_ops,
        )

        self._proposals[proposal.proposal_id] = proposal
        return proposal

    # ── 3. Proposal Retrieval & Lifecycle Management ──────────────────────────

    async def get_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> Optional[DebriefProposal]:
        """
        Retrieves a server-held proposal.
        Enforces user scoping and TTL evaluation.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return None

        if proposal.user_id != user_id:
            logger.warning("Cross-user access blocked: proposal user '%s' != '%s'", proposal.user_id, user_id)
            return None

        # Check TTL
        now_utc = datetime.now(timezone.utc)
        if now_utc > proposal.expires_at and proposal.status in (DebriefProposalStatus.DRAFT, DebriefProposalStatus.APPROVED):
            validate_debrief_state_transition(proposal.status, DebriefProposalStatus.EXPIRED)
            proposal.status = DebriefProposalStatus.EXPIRED
            proposal.failure_reason = DebriefFailureReason.EXPIRED

        return proposal

    async def abort_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
    ) -> DebriefProposal:
        """
        Explicitly aborts/rejects a proposal (DRAFT -> ABORTED).
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError(f"Debrief proposal '{proposal_id}' not found.")

        if proposal.user_id != user_id:
            raise ValueError(f"Cross-user debrief access rejected for proposal '{proposal_id}'.")

        validate_debrief_state_transition(proposal.status, DebriefProposalStatus.ABORTED)
        proposal.status = DebriefProposalStatus.ABORTED
        return proposal

    # ── 4. Two-Tier Governed Execution ────────────────────────────────────────

    async def execute_proposal(
        self,
        proposal_id: str,
        user_id: str = DEFAULT_LOCAL_USER_ID,
        approved: bool = True,
    ) -> DebriefProposal:
        """
        Executes an approved proposal through two-tier validation:
        Tier 1 (APPROVED): Global preflight checks (TTL, permissions, state hash, preconditions).
               If preflight fails: APPROVED -> FAILED (zero mutations executed).
        Tier 2 (EXECUTING): Sequential operation dispatch.
               Operation 1 failure: EXECUTING -> FAILED (zero committed mutations).
               Operation K > 1 failure: EXECUTING -> PARTIALLY_EXECUTED (prior ops committed).
               All succeed: EXECUTING -> COMPLETED.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise ValueError(f"Debrief proposal '{proposal_id}' not found.")

        if proposal.user_id != user_id:
            raise ValueError(f"Cross-user debrief access rejected for proposal '{proposal_id}'.")

        # User rejection
        if not approved:
            validate_debrief_state_transition(proposal.status, DebriefProposalStatus.ABORTED)
            proposal.status = DebriefProposalStatus.ABORTED
            return proposal

        now_utc = datetime.now(timezone.utc)

        # User approval
        if proposal.status == DebriefProposalStatus.DRAFT:
            # Check TTL for DRAFT proposal before confirmation (DRAFT -> EXPIRED)
            if now_utc > proposal.expires_at:
                validate_debrief_state_transition(proposal.status, DebriefProposalStatus.EXPIRED)
                proposal.status = DebriefProposalStatus.EXPIRED
                proposal.failure_reason = DebriefFailureReason.EXPIRED
                raise ValueError(f"Debrief proposal '{proposal_id}' has expired.")
            validate_debrief_state_transition(proposal.status, DebriefProposalStatus.APPROVED)
            proposal.status = DebriefProposalStatus.APPROVED

        if proposal.status != DebriefProposalStatus.APPROVED:
            raise ValueError(f"Cannot execute proposal in status '{proposal.status.value}'. Must be APPROVED.")

        # ── TIER 1: GLOBAL PREFLIGHT VALIDATION (In APPROVED state) ───────────
        # 1. TTL check for APPROVED proposal before execution starts (APPROVED -> EXPIRED)
        if now_utc > proposal.expires_at:
            validate_debrief_state_transition(proposal.status, DebriefProposalStatus.EXPIRED)
            proposal.status = DebriefProposalStatus.EXPIRED
            proposal.failure_reason = DebriefFailureReason.EXPIRED
            raise ValueError(f"Debrief proposal '{proposal_id}' has expired.")

        # 2. Permissions check
        # NOTE: PermissionType in Project Aurora defines: LOCATION, CALENDAR, NOTIFICATIONS,
        # CONTACTS, REMINDERS, PHOTOS_FILES, TASKS, COMMITMENTS.
        # PermissionType.MEMORY does not exist and is not required: memory creation is governed
        # by authenticated server identity, user confirmation of the proposal, server-held
        # immutable precondition, MemoryManager authority, and server-derived USER_EXPLICIT provenance.
        has_tasks = any(op.operation_type == DebriefOperationType.CREATE_TASK for op in proposal.ordered_operations)
        has_reminders = any(op.operation_type == DebriefOperationType.CREATE_REMINDER for op in proposal.ordered_operations)

        if has_tasks:
            is_auth = await self.permission_manager.is_authorized(PermissionType.TASKS, user_id=user_id)
            if not is_auth:
                validate_debrief_state_transition(proposal.status, DebriefProposalStatus.FAILED)
                proposal.status = DebriefProposalStatus.FAILED
                proposal.failure_reason = DebriefFailureReason.INSUFFICIENT_PERMISSIONS
                raise PermissionError("Permission 'tasks' is required to execute debrief proposal.")

        if has_reminders:
            is_auth = await self.permission_manager.is_authorized(PermissionType.REMINDERS, user_id=user_id)
            if not is_auth:
                validate_debrief_state_transition(proposal.status, DebriefProposalStatus.FAILED)
                proposal.status = DebriefProposalStatus.FAILED
                proposal.failure_reason = DebriefFailureReason.INSUFFICIENT_PERMISSIONS
                raise PermissionError("Permission 'reminders' is required to execute debrief proposal.")

        # 3. State Hash Verification
        referenced_task_ids: set[str] = set()
        referenced_commitment_ids: set[str] = set()
        for op in proposal.ordered_operations:
            if op.operation_type == DebriefOperationType.CREATE_TASK and op.precondition.task_create:
                if op.precondition.task_create.exact_commitment_id:
                    referenced_commitment_ids.add(op.precondition.task_create.exact_commitment_id)
            elif op.operation_type == DebriefOperationType.CREATE_REMINDER and op.precondition.reminder_create:
                rc = op.precondition.reminder_create
                if rc.linked_entity_type == ReminderLinkType.TASK and rc.linked_entity_id:
                    referenced_task_ids.add(rc.linked_entity_id)
                elif rc.linked_entity_type == ReminderLinkType.COMMITMENT and rc.linked_entity_id:
                    referenced_commitment_ids.add(rc.linked_entity_id)

        current_tasks: List[Task] = []
        for tid in referenced_task_ids:
            t = await self.task_manager.get_task(task_id=tid, user_id=user_id)
            if t is None:
                validate_debrief_state_transition(proposal.status, DebriefProposalStatus.FAILED)
                proposal.status = DebriefProposalStatus.FAILED
                proposal.failure_reason = DebriefFailureReason.PRECONDITION_FAILED
                raise ValueError(f"Referenced task '{tid}' no longer exists.")
            current_tasks.append(t)

        current_commitments: List[Commitment] = []
        for cid in referenced_commitment_ids:
            c = await self.commitment_manager.get_commitment(commitment_id=cid, user_id=user_id)
            if c is None:
                validate_debrief_state_transition(proposal.status, DebriefProposalStatus.FAILED)
                proposal.status = DebriefProposalStatus.FAILED
                proposal.failure_reason = DebriefFailureReason.PRECONDITION_FAILED
                raise ValueError(f"Referenced commitment '{cid}' no longer exists.")
            current_commitments.append(c)

        current_hash = compute_debrief_state_hash(current_tasks, current_commitments)
        if current_hash != proposal.state_hash:
            validate_debrief_state_transition(proposal.status, DebriefProposalStatus.FAILED)
            proposal.status = DebriefProposalStatus.FAILED
            proposal.failure_reason = DebriefFailureReason.STATE_CHANGED
            raise ValueError("Referenced entity state has changed concurrently. Preflight failed.")

        # ── TIER 1 PASSED: ENTER EXECUTING STATE ───────────────────────────────
        validate_debrief_state_transition(proposal.status, DebriefProposalStatus.EXECUTING)
        proposal.status = DebriefProposalStatus.EXECUTING

        # ── TIER 2: SEQUENTIAL OPERATION DISPATCH ─────────────────────────────
        committed_count = 0

        for i, op in enumerate(proposal.ordered_operations):
            try:
                if op.operation_type == DebriefOperationType.CREATE_TASK:
                    tc = op.precondition.task_create
                    assert tc is not None
                    eff_dur = tc.exact_duration_minutes or 30
                    task = await self.task_manager.create_task(
                        title=tc.exact_title,
                        user_id=user_id,
                        priority=tc.exact_priority,
                        due_at=tc.exact_due_at,
                        source_commitment_id=tc.exact_commitment_id,
                        metadata={
                            "source_event_id": proposal.event_id,
                            "proposal_id": proposal.proposal_id,
                            "duration_minutes": eff_dur,
                        },
                    )
                    op.status = DebriefOperationStatus.COMPLETED
                    op.created_entity_id = task.id
                    committed_count += 1

                elif op.operation_type == DebriefOperationType.CREATE_REMINDER:
                    rc = op.precondition.reminder_create
                    assert rc is not None
                    task_id = rc.linked_entity_id if rc.linked_entity_type == ReminderLinkType.TASK else None
                    commitment_id = rc.linked_entity_id if rc.linked_entity_type == ReminderLinkType.COMMITMENT else None
                    reminder = await self.reminder_manager.create_reminder(
                        title=rc.exact_title,
                        scheduled_at=rc.exact_scheduled_at,
                        user_id=user_id,
                        task_id=task_id,
                        commitment_id=commitment_id,
                    )
                    op.status = DebriefOperationStatus.COMPLETED
                    op.created_entity_id = reminder.id
                    committed_count += 1

                elif op.operation_type == DebriefOperationType.CREATE_MEMORY:
                    mc = op.precondition.memory_create
                    assert mc is not None
                    # Phase 5L Authority: Server unconditionally derives MemorySource.USER_EXPLICIT
                    memory, _ = await self.memory_manager.create_memory(
                        content=mc.exact_content,
                        type=mc.exact_memory_type,
                        importance=3,
                        confidence=mc.exact_confidence,
                        source=MemorySource.USER_EXPLICIT,
                        status=MemoryStatus.ACTIVE,
                        tags=mc.context_tags,
                        metadata={
                            "source_event_id": mc.source_event_id,
                            "proposal_id": proposal.proposal_id,
                            "operation_id": op.operation_id,
                            "provenance": "event_debrief",
                        },
                        user_id=user_id,
                    )
                    op.status = DebriefOperationStatus.COMPLETED
                    op.created_entity_id = memory.id
                    committed_count += 1

            except Exception as exc:
                logger.error("Debrief operation failed at index %d: %s", i, exc, exc_info=True)
                op.status = DebriefOperationStatus.FAILED
                op.failure_error = str(exc)

                # Cancel remaining operations
                for rem_op in proposal.ordered_operations[i + 1:]:
                    rem_op.status = DebriefOperationStatus.SKIPPED

                if committed_count == 0:
                    # Operation 1 failed: zero committed mutations
                    validate_debrief_state_transition(proposal.status, DebriefProposalStatus.FAILED)
                    proposal.status = DebriefProposalStatus.FAILED
                    proposal.failure_reason = DebriefFailureReason.OPERATION_FAILED
                else:
                    # Operation K > 1 failed: prior mutations remain committed
                    validate_debrief_state_transition(proposal.status, DebriefProposalStatus.PARTIALLY_EXECUTED)
                    proposal.status = DebriefProposalStatus.PARTIALLY_EXECUTED
                    proposal.failure_reason = DebriefFailureReason.OPERATION_FAILED

                return proposal

        # All operations succeeded!
        validate_debrief_state_transition(proposal.status, DebriefProposalStatus.COMPLETED)
        proposal.status = DebriefProposalStatus.COMPLETED
        proposal.applied_at = datetime.now(timezone.utc)
        return proposal


# ── Dependency Injection Singletons ───────────────────────────────────────────

_default_briefing_service: Optional[EventBriefingService] = None


def get_briefing_service(
    calendar_manager: Optional[CalendarManager] = None,
    task_manager: Optional[TaskManager] = None,
    commitment_manager: Optional[CommitmentManager] = None,
    reminder_manager: Optional[ReminderManager] = None,
    memory_manager: Optional[MemoryManager] = None,
    permission_manager: Optional[PermissionManager] = None,
) -> EventBriefingService:
    global _default_briefing_service
    if any([calendar_manager, task_manager, commitment_manager, reminder_manager, memory_manager, permission_manager]):
        return EventBriefingService(
            calendar_manager=calendar_manager,
            task_manager=task_manager,
            commitment_manager=commitment_manager,
            reminder_manager=reminder_manager,
            memory_manager=memory_manager,
            permission_manager=permission_manager,
        )
    if _default_briefing_service is None:
        _default_briefing_service = EventBriefingService()
    return _default_briefing_service


def get_briefing_service_dependency() -> EventBriefingService:
    return get_briefing_service()
