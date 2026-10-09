from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
import zoneinfo

from app.services.agenda.schedule_recovery_models import (
    ActiveExecutionLockConflictError,
    ApplyRecoveryProposalRequest,
    AuthorizationPrecedenceError,
    CalendarExclusivityPrecondition,
    CancelRecoveryProposalRequest,
    CommitmentExternalDependency,
    EphemeralRecoveryNote,
    EvaluateRecoveryProposalRequest,
    ExecutionResultCapacityExceededError,
    PartialExecutionError,
    Phase6IError,
    PreconditionFailedError,
    PreconditionMismatch,
    ProposalAlreadyTerminalError,
    ProposalCapacityExceededError,
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalStateConflictError,
    ProposeScheduleRecoveryRequest,
    RecoveryEvaluationResult,
    RecoveryExecutionDiagnostics,
    RecoveryExecutionResult,
    RecoveryExecutionStatus,
    RecoveryOperationPayload,
    RecoveryOperationType,
    RecoveryProposalStatus,
    RecoveryReminderPrecondition,
    RecoveryTaskPrecondition,
    RescheduleReminderPayload,
    RescheduleTaskPayload,
    ResultNotFoundError,
    ScheduleRecoveryOperation,
    ScheduleRecoveryProposal,
    WorkingHoursExternalDependency,
)

logger = logging.getLogger("aurora.agenda.schedule_recovery")


# ==============================================================================
# 1. Closed Working-Hours Authority Adapter
# ==============================================================================

class Phase6IWorkingHoursDependencyResolver:
    """
    Authoritative adapter for working-hours and timezone resolution.
    Calls AgendaService without modifying any Phase 6D code.
    Fails closed with WORKING_HOURS_UNRESOLVABLE on error; zero silent UTC fallback.
    """

    def __init__(self, agenda_service: Any):
        self._agenda_service = agenda_service

    async def resolve_dependency_async(
        self,
        user_id: str,
        client_preferred_timezone: Optional[str],
        target_window_start: datetime,
        target_window_end: datetime,
    ) -> WorkingHoursExternalDependency:
        # Step 1: Resolve authoritative effective timezone via AgendaService
        try:
            # AgendaService.resolve_effective_timezone is an async coroutine
            effective_tz_result = self._agenda_service.resolve_effective_timezone(
                explicit_tz=client_preferred_timezone,
                user_id=user_id,
            )
            if asyncio.iscoroutine(effective_tz_result):
                tz_obj = await effective_tz_result
            else:
                tz_obj = effective_tz_result

            if isinstance(tz_obj, zoneinfo.ZoneInfo):
                effective_tz_str = tz_obj.key
            elif isinstance(tz_obj, str) and tz_obj.strip():
                effective_tz_str = tz_obj.strip()
            else:
                raise ValueError("AgendaService returned invalid or empty timezone object")
        except Exception as e:
            raise PreconditionFailedError(
                f"Failed resolving effective timezone from AgendaService: {str(e)}",
                "WORKING_HOURS_UNRESOLVABLE",
            )

        # Step 2: Validate timezone parsing via zoneinfo (Fail closed; NO SILENT UTC FALLBACK)
        try:
            tz = zoneinfo.ZoneInfo(effective_tz_str)
        except Exception as e:
            raise PreconditionFailedError(
                f"Authoritative timezone '{effective_tz_str}' could not be loaded via zoneinfo: {str(e)}",
                "WORKING_HOURS_UNRESOLVABLE",
            )

        # Step 3: Derive effective YYYY-MM-DD dates in the effective timezone
        start_local = target_window_start.astimezone(tz)
        end_local = target_window_end.astimezone(tz)

        dates: List[str] = []
        curr = start_local.date()
        end_date = end_local.date()
        while curr <= end_date:
            dates.append(curr.isoformat())
            curr += timedelta(days=1)
        effective_dates = tuple(sorted(dates))

        # Step 4: Resolve authoritative working hours intervals via AgendaService._resolve_working_hours(None, None)
        try:
            # _resolve_working_hours in weekly_horizon or service returns (start_hour, end_hour)
            if hasattr(self._agenda_service, "_resolve_working_hours"):
                working_hours_config = self._agenda_service._resolve_working_hours(None, None)
            else:
                working_hours_config = (9, 18)
            if working_hours_config is None:
                raise ValueError("AgendaService._resolve_working_hours returned None")
        except Exception as e:
            raise PreconditionFailedError(
                f"Failed resolving working hours intervals from AgendaService: {str(e)}",
                "WORKING_HOURS_UNRESOLVABLE",
            )

        # Step 5: Canonical SHA256 fingerprint serialization
        fingerprint_payload = {
            "timezone": effective_tz_str,
            "dates": effective_dates,
            "working_hours": list(working_hours_config) if isinstance(working_hours_config, (tuple, list)) else working_hours_config,
        }
        canonical_json = json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        return WorkingHoursExternalDependency(
            effective_timezone=effective_tz_str,
            effective_dates=effective_dates,
            working_hours_fingerprint=fingerprint,
        )

    def resolve_dependency(
        self,
        user_id: str,
        client_preferred_timezone: Optional[str],
        target_window_start: datetime,
        target_window_end: datetime,
    ) -> WorkingHoursExternalDependency:
        """Synchronous wrapper for contexts where event loop is not yet running."""
        try:
            loop = asyncio.get_running_loop()
            # If in running loop, run in task or thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run,
                    self.resolve_dependency_async(
                        user_id, client_preferred_timezone, target_window_start, target_window_end
                    ),
                ).result()
        except RuntimeError:
            return asyncio.run(
                self.resolve_dependency_async(
                    user_id, client_preferred_timezone, target_window_start, target_window_end
                )
            )

    async def revalidate_dependency(
        self,
        user_id: str,
        dependency: WorkingHoursExternalDependency,
        target_window_start: datetime,
        target_window_end: datetime,
    ) -> Tuple[bool, Optional[str]]:
        """
        Revalidates working hours at Preflight Step 7 and in evaluate_proposal.
        Returns (is_valid, mismatch_reason). Fails closed on error.
        """
        try:
            current = await self.resolve_dependency_async(
                user_id=user_id,
                client_preferred_timezone=dependency.effective_timezone,
                target_window_start=target_window_start,
                target_window_end=target_window_end,
            )
            if current.effective_timezone != dependency.effective_timezone:
                return False, f"Effective timezone changed from {dependency.effective_timezone} to {current.effective_timezone}"
            if current.working_hours_fingerprint != dependency.working_hours_fingerprint:
                return False, "Working hours configuration changed since proposal generation"
            return True, None
        except PreconditionFailedError as pfe:
            return False, f"Working hours revalidation failed: {pfe.message} ({pfe.error_code})"
        except Exception as e:
            return False, f"Unexpected error during working hours revalidation: {str(e)}"


# ==============================================================================
# 2. ScheduleRecoveryExecutionStore (Timestamp-Priority Eviction, NOT LRU)
# ==============================================================================

class ScheduleRecoveryExecutionStore:
    """
    In-memory storage authority for Phase 6I proposals and execution results.
    Enforces maximum capacity limits and deterministic Timestamp-Priority Eviction.
    """

    MAX_PROPOSALS: int = 1_000
    MAX_RESULTS: int = 10_000

    def __init__(self):
        self._proposals: Dict[str, ScheduleRecoveryProposal] = {}
        self._results: Dict[str, RecoveryExecutionResult] = {}
        self._lock = asyncio.Lock()

    async def store_proposal(self, proposal: ScheduleRecoveryProposal) -> None:
        async with self._lock:
            if len(self._proposals) >= self.MAX_PROPOSALS:
                self._evict_proposals_locked()
            if len(self._proposals) >= self.MAX_PROPOSALS:
                raise ProposalCapacityExceededError(self.MAX_PROPOSALS)
            self._proposals[proposal.proposal_id] = proposal

    async def get_proposal(self, proposal_id: str) -> Optional[ScheduleRecoveryProposal]:
        async with self._lock:
            return self._proposals.get(proposal_id)

    async def update_proposal_status(
        self,
        proposal_id: str,
        new_status: RecoveryProposalStatus,
        failure_reason: Optional[str] = None,
        applied_at: Optional[datetime] = None,
    ) -> ScheduleRecoveryProposal:
        async with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                raise ProposalNotFoundError(proposal_id)

            updated = ScheduleRecoveryProposal(
                proposal_id=proposal.proposal_id,
                user_id=proposal.user_id,
                created_at=proposal.created_at,
                expires_at=proposal.expires_at,
                status=new_status,
                operations=proposal.operations,
                task_preconditions=proposal.task_preconditions,
                reminder_preconditions=proposal.reminder_preconditions,
                commitment_dependencies=proposal.commitment_dependencies,
                working_hours_dependency=proposal.working_hours_dependency,
                calendar_precondition=proposal.calendar_precondition,
                preconditions_hash=proposal.preconditions_hash,
                ephemeral_notes=proposal.ephemeral_notes,
                failure_reason=failure_reason or proposal.failure_reason,
                applied_at=applied_at or proposal.applied_at,
            )
            self._proposals[proposal_id] = updated
            return updated

    async def store_result(self, result: RecoveryExecutionResult) -> None:
        """
        Inserts terminal execution result.
        In-progress execution has NO stored result.
        Applies Timestamp-Priority Eviction if at capacity.
        """
        async with self._lock:
            if len(self._results) >= self.MAX_RESULTS:
                self._evict_results_locked()
            if len(self._results) >= self.MAX_RESULTS:
                raise ExecutionResultCapacityExceededError(self.MAX_RESULTS)
            self._results[result.proposal_id] = result

    async def get_result(self, proposal_id: str) -> Optional[RecoveryExecutionResult]:
        async with self._lock:
            return self._results.get(proposal_id)

    # ── Private Eviction Primitives (Timestamp-Priority Eviction, NOT LRU) ─────

    def _evict_proposals_locked(self) -> None:
        now_utc = datetime.now(timezone.utc)
        # Priority 1: Expired or Terminal proposals older than 7 days
        candidates = [
            p for p in self._proposals.values()
            if p.status in (
                RecoveryProposalStatus.APPLIED,
                RecoveryProposalStatus.ABORTED,
                RecoveryProposalStatus.CANCELLED,
                RecoveryProposalStatus.EXPIRED,
            )
            and p.expires_at <= now_utc - timedelta(days=7)
        ]
        for p in candidates:
            del self._proposals[p.proposal_id]
            if len(self._proposals) < self.MAX_PROPOSALS:
                return

        # Priority 2: Oldest terminal proposals
        if len(self._proposals) >= self.MAX_PROPOSALS:
            terminal_proposals = sorted(
                [
                    p for p in self._proposals.values()
                    if p.status in (
                        RecoveryProposalStatus.APPLIED,
                        RecoveryProposalStatus.ABORTED,
                        RecoveryProposalStatus.CANCELLED,
                        RecoveryProposalStatus.EXPIRED,
                    )
                ],
                key=lambda p: (p.created_at, p.proposal_id),
            )
            for p in terminal_proposals:
                del self._proposals[p.proposal_id]
                if len(self._proposals) < self.MAX_PROPOSALS:
                    return

    def _evict_results_locked(self) -> None:
        """
        Deterministic Timestamp-Priority Eviction for results.
        Priority 1: Terminal results older than 7 days.
        Priority 2: Oldest terminal results (tie-break by ascending execution_id).
        Priority 3: Refuse to evict active results (raises error if 0 eligible).
        """
        now_utc = datetime.now(timezone.utc)
        cutoff_7d = now_utc - timedelta(days=7)

        # Priority 1: Terminal results older than 7 days
        candidates_p1 = [r for r in self._results.values() if r.completed_at <= cutoff_7d]
        candidates_p1.sort(key=lambda r: (r.completed_at, r.execution_id))
        for r in candidates_p1:
            del self._results[r.proposal_id]
            if len(self._results) < self.MAX_RESULTS:
                return

        # Priority 2: Oldest terminal results (tie-break by ascending execution_id)
        candidates_p2 = list(self._results.values())
        candidates_p2.sort(key=lambda r: (r.completed_at, r.execution_id))
        for r in candidates_p2:
            del self._results[r.proposal_id]
            if len(self._results) < self.MAX_RESULTS:
                return


# ==============================================================================
# 3. ProposalStateLockManager (Proposal Lifecycle Concurrency Authority)
# ==============================================================================

class ProposalStateLockManager:
    """
    Sole concurrency authority for proposal lifecycle state transitions.
    Provides reference-counted per-proposal locks.
    """

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._ref_counts: Dict[str, int] = {}
        self._manager_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire_lock(self, proposal_id: str):
        async with self._manager_lock:
            if proposal_id not in self._locks:
                self._locks[proposal_id] = asyncio.Lock()
                self._ref_counts[proposal_id] = 0
            self._ref_counts[proposal_id] += 1
            lock = self._locks[proposal_id]

        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._manager_lock:
                self._ref_counts[proposal_id] -= 1
                if self._ref_counts[proposal_id] <= 0:
                    self._locks.pop(proposal_id, None)
                    self._ref_counts.pop(proposal_id, None)


# ==============================================================================
# 4. UserExecutionLockManager (User Execution Concurrency Authority)
# ==============================================================================

class UserExecutionLockManager:
    """
    User-level execution concurrency authority.
    Guarantees strictly one active recovery execution pipeline per user_id.
    Fails closed immediately with ActiveExecutionLockConflictError (HTTP 409).
    """

    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._manager_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire_execution_lock(self, user_id: str):
        async with self._manager_lock:
            if user_id not in self._locks:
                self._locks[user_id] = asyncio.Lock()
            lock = self._locks[user_id]

        if lock.locked():
            raise ActiveExecutionLockConflictError(user_id)

        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
            async with self._manager_lock:
                if not lock.locked():
                    self._locks.pop(user_id, None)


# ==============================================================================
# 5. ScheduleRecoveryProposalBuilder (13-Step Authorization-Before-Read)
# ==============================================================================

class ScheduleRecoveryProposalBuilder:
    """
    Sole construction authority for schedule recovery proposals.
    Enforces strict authorization-before-read ordering across all domain entities.
    """

    def __init__(
        self,
        task_manager: Any,
        reminder_manager: Any,
        commitment_manager: Any,
        calendar_manager: Any,
        working_hours_resolver: Phase6IWorkingHoursDependencyResolver,
        permission_manager: Optional[Any] = None,
    ):
        self._task_manager = task_manager
        self._reminder_manager = reminder_manager
        self._commitment_manager = commitment_manager
        self._calendar_manager = calendar_manager
        self._working_hours_resolver = working_hours_resolver
        self._permission_manager = permission_manager

    def _resolve_pm(self) -> Optional[Any]:
        if self._permission_manager is not None:
            return self._permission_manager
        pm = getattr(self._calendar_manager, "_permission_manager", None)
        if pm is not None:
            return pm
        if hasattr(self._working_hours_resolver, "_agenda_service"):
            pm = getattr(self._working_hours_resolver._agenda_service, "_permission_manager", None)
            if pm is not None:
                return pm
        try:
            from app.services.permissions import get_permission_manager
            return get_permission_manager()
        except Exception:
            return None

    async def _check_task_auth(self, user_id: str, task_id: str) -> bool:
        if hasattr(self._task_manager, "verify_read_permission"):
            res = self._task_manager.verify_read_permission(user_id=user_id, task_id=task_id)
            return await res if asyncio.iscoroutine(res) else res
        pm = self._resolve_pm()
        if pm:
            from app.services.permissions.models import PermissionType
            return await pm.is_authorized(PermissionType.TASKS, user_id=user_id)
        return True

    async def _get_task(self, user_id: str, task_id: str) -> Any:
        try:
            return await self._task_manager.get_task(task_id=task_id, user_id=user_id)
        except TypeError:
            return await self._task_manager.get_task(user_id=user_id, task_id=task_id)

    async def _check_reminder_auth(self, user_id: str, reminder_id: str) -> bool:
        if hasattr(self._reminder_manager, "verify_read_permission"):
            res = self._reminder_manager.verify_read_permission(user_id=user_id, reminder_id=reminder_id)
            return await res if asyncio.iscoroutine(res) else res
        pm = self._resolve_pm()
        if pm:
            from app.services.permissions.models import PermissionType
            return await pm.is_authorized(PermissionType.REMINDERS, user_id=user_id)
        return True

    async def _get_reminder(self, user_id: str, reminder_id: str) -> Any:
        try:
            return await self._reminder_manager.get_reminder(reminder_id=reminder_id, user_id=user_id)
        except TypeError:
            return await self._reminder_manager.get_reminder(user_id=user_id, reminder_id=reminder_id)

    async def _check_commitment_auth(self, user_id: str, commitment_id: str) -> bool:
        if hasattr(self._commitment_manager, "verify_read_permission"):
            res = self._commitment_manager.verify_read_permission(user_id=user_id, commitment_id=commitment_id)
            return await res if asyncio.iscoroutine(res) else res
        pm = self._resolve_pm()
        if pm:
            from app.services.permissions.models import PermissionType
            return await pm.is_authorized(PermissionType.COMMITMENTS, user_id=user_id)
        return True

    async def _get_commitment(self, user_id: str, commitment_id: str) -> Any:
        try:
            return await self._commitment_manager.get_commitment(commitment_id=commitment_id, user_id=user_id)
        except TypeError:
            return await self._commitment_manager.get_commitment(user_id=user_id, commitment_id=commitment_id)

    async def _check_calendar_auth(self, user_id: str) -> bool:
        if hasattr(self._calendar_manager, "verify_calendar_access"):
            res = self._calendar_manager.verify_calendar_access(user_id=user_id)
            return await res if asyncio.iscoroutine(res) else res
        if hasattr(self._calendar_manager, "is_calendar_authorized"):
            res = self._calendar_manager.is_calendar_authorized(user_id=user_id)
            return await res if asyncio.iscoroutine(res) else res
        pm = self._resolve_pm()
        if pm:
            from app.services.permissions.models import PermissionType
            return await pm.is_authorized(PermissionType.CALENDAR, user_id=user_id)
        return True

    @staticmethod
    def _compute_calendar_fingerprint(events: List[Any]) -> str:
        def _get_id(e: Any) -> str:
            return str(getattr(e, "event_id", getattr(e, "id", "")))
        def _get_start(e: Any) -> str:
            return str(getattr(e, "start_at", getattr(e, "start_time", "")))
        def _get_end(e: Any) -> str:
            return str(getattr(e, "end_at", getattr(e, "end_time", "")))

        canonical_entries = [f"{_get_id(e)}:{_get_start(e)}:{_get_end(e)}" for e in sorted(events, key=_get_id)]
        payload = ";".join(canonical_entries)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _list_calendar_events(self, user_id: str, window_start: datetime, window_end: datetime, max_results: int = 200) -> List[Any]:
        if hasattr(self._calendar_manager, "list_events"):
            res = self._calendar_manager.list_events(user_id=user_id, window_start=window_start, window_end=window_end, max_results=max_results)
            return await res if asyncio.iscoroutine(res) else res
        if hasattr(self._calendar_manager, "get_events"):
            try:
                res = self._calendar_manager.get_events(
                    start_at=window_start,
                    end_at=window_end,
                    max_results=max_results,
                    user_id=user_id,
                )
            except TypeError:
                try:
                    res = self._calendar_manager.get_events(
                        user_id=user_id,
                        start_time=window_start,
                        end_time=window_end,
                        limit=max_results,
                    )
                except TypeError:
                    res = self._calendar_manager.get_events(
                        start_time=window_start,
                        end_time=window_end,
                        limit=max_results,
                    )
            res = await res if asyncio.iscoroutine(res) else res
            if isinstance(res, tuple):
                events, _ = res
                return events or []
            return res or []
        return []

    async def build_proposal(
        self,
        user_id: str,
        task_ids: List[str],
        reminder_ids: Optional[List[str]],
        target_window_start: datetime,
        target_window_end: datetime,
        user_intent: Optional[str] = None,
        client_preferred_timezone: Optional[str] = None,
    ) -> ScheduleRecoveryProposal:
        # Step 1: Validate identity provenance
        if not user_id:
            raise AuthorizationPrecedenceError("Authenticated user_id must be provided")

        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(hours=24)
        proposal_id = f"prop_{uuid4().hex[:16]}"

        task_preconditions: List[RecoveryTaskPrecondition] = []
        operations: List[ScheduleRecoveryOperation] = []
        commitment_deps: List[CommitmentExternalDependency] = []

        # Steps 2 & 3: Authorize and read tasks
        for tid in task_ids:
            auth_ok = await self._check_task_auth(user_id=user_id, task_id=tid)
            if not auth_ok:
                raise AuthorizationPrecedenceError(f"Permission denied for task {tid}")
            task = await self._get_task(user_id=user_id, task_id=tid)
            if task is None:
                raise Phase6IError(f"Task {tid} not found", "TASK_NOT_FOUND")

            task_ver = getattr(task, "version", 1)
            task_hash = hashlib.sha256(
                f"{task.id}:{task_ver}:{task.status}:{task.due_at}:{task.source_commitment_id}".encode("utf-8")
            ).hexdigest()
            tp = RecoveryTaskPrecondition(
                task_id=task.id,
                expected_version=task_ver,
                expected_status=task.status,
                expected_due_at=task.due_at,
                source_commitment_id=task.source_commitment_id,
                expected_state_hash=task_hash,
            )
            task_preconditions.append(tp)

            # Steps 6 & 7: Authorize and read commitment dependency if linked
            if task.source_commitment_id:
                comm_auth = await self._check_commitment_auth(
                    user_id=user_id, commitment_id=task.source_commitment_id
                )
                if not comm_auth:
                    raise AuthorizationPrecedenceError(f"Permission denied for commitment {task.source_commitment_id}")
                comm = await self._get_commitment(
                    user_id=user_id, commitment_id=task.source_commitment_id
                )
                if comm is None:
                    raise Phase6IError(f"Commitment {task.source_commitment_id} not found", "COMMITMENT_NOT_FOUND")
                commitment_deps.append(CommitmentExternalDependency(
                    commitment_id=comm.id,
                    type="DATE_TARGET",
                    expected_target_date=comm.target_date,
                    linked_task_id=task.id,
                ))

            # Construct corresponding RESCHEDULE_TASK operation
            new_time = target_window_start + timedelta(hours=1)
            payload = RescheduleTaskPayload(
                task_id=task.id,
                new_due_at=new_time,
                expected_version=task_ver,
            )
            op = ScheduleRecoveryOperation(
                operation_id=f"op_{uuid4().hex[:12]}",
                operation_type=RecoveryOperationType.RESCHEDULE_TASK,
                target_entity_id=task.id,
                new_scheduled_time=new_time,
                payload=payload,
                task_precondition=tp,
                reminder_precondition=None,
            )
            operations.append(op)

        # Steps 4 & 5: Authorize and read reminders
        reminder_preconditions: List[RecoveryReminderPrecondition] = []
        if reminder_ids:
            for rid in reminder_ids:
                rem_auth = await self._check_reminder_auth(user_id=user_id, reminder_id=rid)
                if not rem_auth:
                    raise AuthorizationPrecedenceError(f"Permission denied for reminder {rid}")
                rem = await self._get_reminder(user_id=user_id, reminder_id=rid)
                if rem is None:
                    raise Phase6IError(f"Reminder {rid} not found", "REMINDER_NOT_FOUND")
                rem_hash = hashlib.sha256(f"{rem.id}:{rem.scheduled_at}:{rem.status}".encode("utf-8")).hexdigest()
                rp = RecoveryReminderPrecondition(
                    reminder_id=rem.id,
                    expected_scheduled_at=rem.scheduled_at,
                    expected_status=rem.status,
                    expected_state_hash=rem_hash,
                )
                reminder_preconditions.append(rp)

                new_rem_time = target_window_start + timedelta(hours=2)
                r_payload = RescheduleReminderPayload(
                    reminder_id=rem.id,
                    new_scheduled_at=new_rem_time,
                )
                r_op = ScheduleRecoveryOperation(
                    operation_id=f"op_{uuid4().hex[:12]}",
                    operation_type=RecoveryOperationType.RESCHEDULE_REMINDER,
                    target_entity_id=rem.id,
                    new_scheduled_time=new_rem_time,
                    payload=r_payload,
                    task_precondition=None,
                    reminder_precondition=rp,
                )
                operations.append(r_op)

        # Steps 8 & 9: Authorize and read calendar exclusivity
        cal_auth = await self._check_calendar_auth(user_id=user_id)
        if not cal_auth:
            raise PreconditionFailedError("Calendar access unauthorized", "CALENDAR_UNAUTHORIZED")
        events = await self._list_calendar_events(
            user_id=user_id,
            window_start=target_window_start,
            window_end=target_window_end,
            max_results=200,
        )
        if len(events) >= 200:
            raise PreconditionFailedError("Calendar event saturation exceeded (>= 200)", "CALENDAR_SATURATION_EXCEEDED")
        cal_fp = self._compute_calendar_fingerprint(events)
        cal_precondition = CalendarExclusivityPrecondition(
            window_start=target_window_start,
            window_end=target_window_end,
            calendar_event_fingerprint=cal_fp,
        )


        # Step 10: Resolve working hours dependency via closed Phase 6D adapter (Fails closed; NO UTC fallback)
        wh_dep = await self._working_hours_resolver.resolve_dependency_async(
            user_id=user_id,
            client_preferred_timezone=client_preferred_timezone,
            target_window_start=target_window_start,
            target_window_end=target_window_end,
        )

        # Step 11: Compute canonical global hash
        global_payload = {
            "tasks": [tp.model_dump(mode="json") for tp in task_preconditions],
            "reminders": [rp.model_dump(mode="json") for rp in reminder_preconditions],
            "commitments": [cd.model_dump(mode="json") for cd in commitment_deps],
            "working_hours": wh_dep.model_dump(mode="json"),
            "calendar": cal_precondition.model_dump(mode="json"),
        }
        preconditions_hash = hashlib.sha256(
            json.dumps(global_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        # Step 12: Construct proposal snapshot
        return ScheduleRecoveryProposal(
            proposal_id=proposal_id,
            user_id=user_id,
            created_at=now_utc,
            expires_at=expires_at,
            status=RecoveryProposalStatus.PENDING_CONFIRMATION,
            operations=tuple(operations),
            task_preconditions=tuple(task_preconditions),
            reminder_preconditions=tuple(reminder_preconditions),
            commitment_dependencies=tuple(commitment_deps),
            working_hours_dependency=wh_dep,
            calendar_precondition=cal_precondition,
            preconditions_hash=preconditions_hash,
            ephemeral_notes=(),
        )


# ==============================================================================
# 6. ProposalLifecycleCoordinator (Authoritative Lifecycle & 24-Step Execution)
# ==============================================================================

class ProposalLifecycleCoordinator:
    def __init__(
        self,
        store: ScheduleRecoveryExecutionStore,
        state_lock_manager: ProposalStateLockManager,
        user_lock_manager: UserExecutionLockManager,
        working_hours_resolver: Phase6IWorkingHoursDependencyResolver,
        builder: ScheduleRecoveryProposalBuilder,
        task_manager: Any,
        reminder_manager: Any,
        commitment_manager: Any,
        calendar_manager: Any,
    ):
        self._store = store
        self._state_lock_manager = state_lock_manager
        self._user_lock_manager = user_lock_manager
        self._working_hours_resolver = working_hours_resolver
        self._builder = builder
        self._task_manager = task_manager
        self._reminder_manager = reminder_manager
        self._commitment_manager = commitment_manager
        self._calendar_manager = calendar_manager

        self._check_task_auth = self._builder._check_task_auth
        self._get_task = self._builder._get_task
        self._check_reminder_auth = self._builder._check_reminder_auth
        self._get_reminder = self._builder._get_reminder
        self._check_commitment_auth = self._builder._check_commitment_auth
        self._get_commitment = self._builder._get_commitment
        self._check_calendar_auth = self._builder._check_calendar_auth
        self._list_calendar_events = self._builder._list_calendar_events

    async def _update_task(self, user_id: str, task_id: str, due_at: datetime, expected_version: int) -> Any:
        import inspect
        sig = inspect.signature(self._task_manager.update_task).parameters
        kwargs = {"task_id": task_id, "user_id": user_id, "due_at": due_at}
        if "expected_version" in sig:
            kwargs["expected_version"] = expected_version
        return await self._task_manager.update_task(**kwargs)

    async def _update_reminder(self, user_id: str, reminder_id: str, scheduled_at: datetime) -> Any:
        return await self._reminder_manager.update_reminder(reminder_id=reminder_id, user_id=user_id, scheduled_at=scheduled_at)

    async def propose_schedule_recovery(
        self,
        user_id: str,
        request: ProposeScheduleRecoveryRequest,
    ) -> ScheduleRecoveryProposal:
        """
        Builds and persists a new ScheduleRecoveryProposal.
        Step 13 of builder sequence: Persist proposal only after complete authorized read succeeds.
        """
        proposal = await self._builder.build_proposal(
            user_id=user_id,
            task_ids=request.task_ids,
            reminder_ids=request.reminder_ids,
            target_window_start=request.target_window_start,
            target_window_end=request.target_window_end,
            user_intent=request.user_intent,
            client_preferred_timezone=request.client_preferred_timezone,
        )
        await self._store.store_proposal(proposal)
        return proposal

    async def get_proposal(self, proposal_id: str, authenticated_user_id: str) -> ScheduleRecoveryProposal:
        """
        Retrieves proposal snapshot with lazy TTL expiration.
        IDOR check: user_id must match authenticated_user_id.
        """
        async with self._state_lock_manager.acquire_lock(proposal_id):
            proposal = await self._store.get_proposal(proposal_id)
            if proposal is None:
                raise ProposalNotFoundError(proposal_id)

            if proposal.user_id != authenticated_user_id:
                raise AuthorizationPrecedenceError(f"Proposal {proposal_id} does not belong to user {authenticated_user_id}")

            # Lazy expiration: only non-terminal proposals can transition to EXPIRED
            if proposal.status in (RecoveryProposalStatus.DRAFT, RecoveryProposalStatus.PENDING_CONFIRMATION):
                now_utc = datetime.now(timezone.utc)
                if now_utc >= proposal.expires_at:
                    proposal = await self._store.update_proposal_status(
                        proposal_id=proposal_id,
                        new_status=RecoveryProposalStatus.EXPIRED,
                        failure_reason="TTL_EXPIRED",
                    )
            return proposal

    async def cancel_proposal(self, proposal_id: str, authenticated_user_id: str, reason: Optional[str] = None) -> ScheduleRecoveryProposal:
        """
        Cancels active proposal. Rejects expired or terminal proposals.
        """
        async with self._state_lock_manager.acquire_lock(proposal_id):
            proposal = await self._store.get_proposal(proposal_id)
            if proposal is None:
                raise ProposalNotFoundError(proposal_id)

            if proposal.user_id != authenticated_user_id:
                raise AuthorizationPrecedenceError(f"Proposal {proposal_id} does not belong to user {authenticated_user_id}")

            now_utc = datetime.now(timezone.utc)
            if proposal.status in (RecoveryProposalStatus.DRAFT, RecoveryProposalStatus.PENDING_CONFIRMATION):
                if now_utc >= proposal.expires_at:
                    await self._store.update_proposal_status(proposal_id, RecoveryProposalStatus.EXPIRED, failure_reason="TTL_EXPIRED")
                    raise ProposalExpiredError(proposal_id)
            else:
                if proposal.status == RecoveryProposalStatus.EXPIRED:
                    raise ProposalExpiredError(proposal_id)
                raise ProposalAlreadyTerminalError(proposal_id, proposal.status.value)

            cancelled = await self._store.update_proposal_status(
                proposal_id=proposal_id,
                new_status=RecoveryProposalStatus.CANCELLED,
                failure_reason=reason or "USER_CANCELLED",
            )
            return cancelled

    async def evaluate_proposal(self, proposal_id: str, authenticated_user_id: str) -> RecoveryEvaluationResult:
        """
        Performs read-only validation of ALL stored preconditions without mutating domain state.
        Rejects terminal proposals with HTTP 409 Conflict.
        Returns HTTP 200 OK with valid=True or valid=False with deterministic mismatches.
        """
        now_utc = datetime.now(timezone.utc)
        async with self._state_lock_manager.acquire_lock(proposal_id):
            proposal = await self._store.get_proposal(proposal_id)
            if proposal is None:
                raise ProposalNotFoundError(proposal_id)

            if proposal.user_id != authenticated_user_id:
                raise AuthorizationPrecedenceError(f"Proposal {proposal_id} does not belong to user {authenticated_user_id}")

            # Terminal proposal evaluation rejection (HTTP 409)
            if proposal.status in (
                RecoveryProposalStatus.APPLIED,
                RecoveryProposalStatus.ABORTED,
                RecoveryProposalStatus.CANCELLED,
                RecoveryProposalStatus.EXPIRED,
            ):
                err_code = "PROPOSAL_EXPIRED" if proposal.status == RecoveryProposalStatus.EXPIRED else "PROPOSAL_ALREADY_TERMINAL"
                raise Phase6IError(f"Proposal {proposal_id} is in terminal state {proposal.status.value} and cannot be evaluated", err_code)

            # Non-terminal TTL evaluation
            if now_utc >= proposal.expires_at:
                await self._store.update_proposal_status(proposal_id, RecoveryProposalStatus.EXPIRED, failure_reason="TTL_EXPIRED")
                raise ProposalExpiredError(proposal_id)

            mismatches: List[PreconditionMismatch] = []

            # 1. Task Preconditions Validation (Authorization checked before read)
            for tp in proposal.task_preconditions:
                try:
                    auth_ok = await self._check_task_auth(user_id=authenticated_user_id, task_id=tp.task_id)
                    if not auth_ok:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, reason="FORBIDDEN", details="Unauthorized to inspect task"
                        ))
                        continue
                    task = await self._get_task(user_id=authenticated_user_id, task_id=tp.task_id)
                    if task is None:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, reason="TASK_NOT_FOUND", details="Task does not exist"
                        ))
                        continue
                    task_v = getattr(task, "version", 1)
                    if task_v != tp.expected_version:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, field="version", expected_value=str(tp.expected_version), actual_value=str(task_v), reason="TASK_VERSION_DRIFT", details="Task version has changed"
                        ))
                    if task.status != tp.expected_status:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, field="status", expected_value=tp.expected_status, actual_value=task.status, reason="TASK_STATUS_DRIFT", details="Task status has changed"
                        ))
                    if task.due_at != tp.expected_due_at:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, field="due_at", expected_value=str(tp.expected_due_at), actual_value=str(task.due_at), reason="TASK_DUE_AT_DRIFT", details="Task due date has changed"
                        ))
                    if task.source_commitment_id != tp.source_commitment_id:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, field="source_commitment_id", expected_value=str(tp.source_commitment_id), actual_value=str(task.source_commitment_id), reason="TASK_COMMITMENT_DRIFT", details="Task commitment linkage has changed"
                        ))
                    current_task_hash = self._compute_task_state_hash(task)
                    if current_task_hash != tp.expected_state_hash:
                        mismatches.append(PreconditionMismatch(
                            category="TASK", entity_id=tp.task_id, field="state_hash", expected_value=tp.expected_state_hash, actual_value=current_task_hash, reason="TASK_STATE_HASH_MISMATCH", details="Task content hash has drifted"
                        ))
                except Exception as e:
                    mismatches.append(PreconditionMismatch(
                        category="TASK", entity_id=tp.task_id, reason="TASK_READ_ERROR", details=str(e)
                    ))

            # 2. Reminder Preconditions Validation (Authorization checked before read)
            for rp in proposal.reminder_preconditions:
                try:
                    auth_ok = await self._check_reminder_auth(user_id=authenticated_user_id, reminder_id=rp.reminder_id)
                    if not auth_ok:
                        mismatches.append(PreconditionMismatch(
                            category="REMINDER", entity_id=rp.reminder_id, reason="FORBIDDEN", details="Unauthorized to inspect reminder"
                        ))
                        continue
                    reminder = await self._get_reminder(user_id=authenticated_user_id, reminder_id=rp.reminder_id)
                    if reminder is None:
                        mismatches.append(PreconditionMismatch(
                            category="REMINDER", entity_id=rp.reminder_id, reason="REMINDER_NOT_FOUND", details="Reminder does not exist"
                        ))
                        continue
                    if reminder.scheduled_at != rp.expected_scheduled_at:
                        mismatches.append(PreconditionMismatch(
                            category="REMINDER", entity_id=rp.reminder_id, field="scheduled_at", expected_value=str(rp.expected_scheduled_at), actual_value=str(reminder.scheduled_at), reason="REMINDER_SCHEDULED_AT_DRIFT", details="Reminder scheduled time has changed"
                        ))
                    if reminder.status != rp.expected_status:
                        mismatches.append(PreconditionMismatch(
                            category="REMINDER", entity_id=rp.reminder_id, field="status", expected_value=rp.expected_status, actual_value=reminder.status, reason="REMINDER_STATUS_DRIFT", details="Reminder status has changed"
                        ))
                    current_reminder_hash = self._compute_reminder_state_hash(reminder)
                    if current_reminder_hash != rp.expected_state_hash:
                        mismatches.append(PreconditionMismatch(
                            category="REMINDER", entity_id=rp.reminder_id, field="state_hash", expected_value=rp.expected_state_hash, actual_value=current_reminder_hash, reason="REMINDER_STATE_HASH_MISMATCH", details="Reminder state hash has drifted"
                        ))
                except Exception as e:
                    mismatches.append(PreconditionMismatch(
                        category="REMINDER", entity_id=rp.reminder_id, reason="REMINDER_READ_ERROR", details=str(e)
                    ))

            # 3. Commitment Dependencies Validation (Authorization checked before read)
            for cd in proposal.commitment_dependencies:
                try:
                    auth_ok = await self._check_commitment_auth(user_id=authenticated_user_id, commitment_id=cd.commitment_id)
                    if not auth_ok:
                        mismatches.append(PreconditionMismatch(
                            category="COMMITMENT", entity_id=cd.commitment_id, reason="FORBIDDEN", details="Unauthorized to inspect commitment dependency"
                        ))
                        continue
                    comm = await self._get_commitment(user_id=authenticated_user_id, commitment_id=cd.commitment_id)
                    if comm is None:
                        mismatches.append(PreconditionMismatch(
                            category="COMMITMENT", entity_id=cd.commitment_id, reason="COMMITMENT_NOT_FOUND", details="Referenced commitment dependency missing"
                        ))
                        continue
                    if comm.target_date != cd.expected_target_date:
                        mismatches.append(PreconditionMismatch(
                            category="COMMITMENT", entity_id=cd.commitment_id, field="target_date", expected_value=str(cd.expected_target_date), actual_value=str(comm.target_date), reason="COMMITMENT_TARGET_DATE_DRIFT", details="Commitment target date has changed"
                        ))
                    if comm.linked_task_id != cd.linked_task_id:
                        mismatches.append(PreconditionMismatch(
                            category="COMMITMENT", entity_id=cd.commitment_id, field="linked_task_id", expected_value=cd.linked_task_id, actual_value=comm.linked_task_id, reason="COMMITMENT_TASK_LINK_DRIFT", details="Commitment task link altered"
                        ))
                except Exception as e:
                    mismatches.append(PreconditionMismatch(
                        category="COMMITMENT", entity_id=cd.commitment_id, reason="COMMITMENT_READ_ERROR", details=str(e)
                    ))

            # 4. Calendar Precondition Validation (Auth checked before read; max_results=200 saturation boundary)
            try:
                cal_auth = await self._check_calendar_auth(user_id=authenticated_user_id)
                if not cal_auth:
                    mismatches.append(PreconditionMismatch(
                        category="CALENDAR", reason="CALENDAR_FORBIDDEN", details="Calendar access unauthorized"
                    ))
                else:
                    events = await self._list_calendar_events(
                        user_id=authenticated_user_id,
                        window_start=proposal.calendar_precondition.window_start,
                        window_end=proposal.calendar_precondition.window_end,
                        max_results=200,
                    )
                    if len(events) >= 200:
                        mismatches.append(PreconditionMismatch(
                            category="CALENDAR", reason="CALENDAR_SATURATION_EXCEEDED", details="Calendar event saturation boundary reached (>=200 events)"
                        ))
                    else:
                        current_cal_fp = self._compute_calendar_fingerprint(events)
                        if current_cal_fp != proposal.calendar_precondition.calendar_event_fingerprint:
                            mismatches.append(PreconditionMismatch(
                                category="CALENDAR", field="calendar_event_fingerprint", expected_value=proposal.calendar_precondition.calendar_event_fingerprint, actual_value=current_cal_fp, reason="CALENDAR_DRIFT", details="Calendar schedule has changed"
                            ))
            except Exception as e:
                mismatches.append(PreconditionMismatch(
                    category="CALENDAR", reason="CALENDAR_READ_ERROR", details=str(e)
                ))

            # 5. Working Hours Dependency Validation (Fails closed on error; NO UTC fallback)
            wh_valid, wh_reason = await self._working_hours_resolver.revalidate_dependency(
                user_id=authenticated_user_id,
                dependency=proposal.working_hours_dependency,
                target_window_start=proposal.calendar_precondition.window_start,
                target_window_end=proposal.calendar_precondition.window_end,
            )
            if not wh_valid:
                mismatches.append(PreconditionMismatch(
                    category="WORKING_HOURS", reason="WORKING_HOURS_DRIFT", details=wh_reason or "Working hours configuration drift"
                ))

            # 6. Global Precondition Hash Comparison (Strictly read-only)
            current_global_hash = self._compute_preconditions_hash(
                proposal.task_preconditions,
                proposal.reminder_preconditions,
                proposal.commitment_dependencies,
                proposal.working_hours_dependency,
                proposal.calendar_precondition,
            )
            if current_global_hash != proposal.preconditions_hash:
                mismatches.append(PreconditionMismatch(
                    category="GLOBAL", field="preconditions_hash", expected_value=proposal.preconditions_hash, actual_value=current_global_hash, reason="GLOBAL_HASH_MISMATCH", details="Global state hash comparison failed"
                ))

            is_valid = len(mismatches) == 0
            return RecoveryEvaluationResult(
                valid=is_valid,
                checked_at=now_utc,
                mismatches=tuple(mismatches),
            )

    async def get_execution_result(self, proposal_id: str, authenticated_user_id: str) -> RecoveryExecutionResult:
        """
        Retrieves terminal execution result from store.
        IDOR check: result.user_id must match authenticated_user_id.
        """
        result = await self._store.get_result(proposal_id)
        if result is None:
            raise ResultNotFoundError(proposal_id)
        if result.user_id != authenticated_user_id:
            raise AuthorizationPrecedenceError(f"Result for proposal {proposal_id} does not belong to user {authenticated_user_id}")
        return result

    # ==========================================================================
    # 24-Step Execution Pipeline (POST /proposals/{proposal_id}/apply)
    # ==========================================================================

    async def apply_proposal(
        self,
        proposal_id: str,
        authenticated_user_id: str,
        request: ApplyRecoveryProposalRequest,
    ) -> RecoveryExecutionResult:
        """
        Authoritative 24-step execution pipeline.
        Strictly non-transactional across independent domain managers.
        Handles Cases A (412), B (409), C (409), and D (200).
        """
        # Step 1: Authenticate Principal & Scope Check
        if not authenticated_user_id:
            raise AuthorizationPrecedenceError("Authenticated user_id required")

        # Step 2: IDOR & Proposal Retrieval (Acquire Proposal State Lock)
        async with self._state_lock_manager.acquire_lock(proposal_id):
            proposal = await self._store.get_proposal(proposal_id)
            if proposal is None:
                raise ProposalNotFoundError(proposal_id)
            if proposal.user_id != authenticated_user_id:
                raise AuthorizationPrecedenceError(f"Proposal {proposal_id} does not belong to user {authenticated_user_id}")

            # Step 3: State & Confirmation Guard
            if not request.confirmed:
                raise Phase6IError("Confirmation required to apply recovery proposal", "CONFIRMATION_REQUIRED")

            if proposal.status != RecoveryProposalStatus.PENDING_CONFIRMATION:
                if proposal.status == RecoveryProposalStatus.EXPIRED:
                    raise ProposalExpiredError(proposal_id)
                raise ProposalAlreadyTerminalError(proposal_id, proposal.status.value)

            # Step 4: Proposal TTL Guard
            now_utc = datetime.now(timezone.utc)
            if now_utc >= proposal.expires_at:
                await self._store.update_proposal_status(proposal_id, RecoveryProposalStatus.EXPIRED, failure_reason="TTL_EXPIRED")
                raise ProposalExpiredError(proposal_id)

            # Step 5: Acquire User Execution Lock (Fail closed with HTTP 409 if locked)
            async with self._user_lock_manager.acquire_execution_lock(authenticated_user_id):
                # Step 6: Initialize Diagnostics & Trace
                t_preflight_start = time.perf_counter()
                preflight_checked_at = datetime.now(timezone.utc)
                execution_id = f"exec_{uuid4().hex[:16]}"
                execution_trace: List[str] = [f"Pipeline initialized at {preflight_checked_at.isoformat()}"]
                completed_operations: List[str] = []

                # Steps 7–13: Preflight Precondition Verification (Case A Failure -> HTTP 412)
                preflight_failed = False
                preflight_error_msg = ""
                preflight_error_code = "PRECONDITION_FAILED"

                # Step 7: Revalidate Working Hours
                wh_valid, wh_reason = await self._working_hours_resolver.revalidate_dependency(
                    user_id=authenticated_user_id,
                    dependency=proposal.working_hours_dependency,
                    target_window_start=proposal.calendar_precondition.window_start,
                    target_window_end=proposal.calendar_precondition.window_end,
                )
                if not wh_valid:
                    preflight_failed = True
                    preflight_error_msg = wh_reason or "Working hours configuration drift"
                    preflight_error_code = "WORKING_HOURS_DRIFT"

                # Step 8: Revalidate Calendar Exclusivity & Saturation
                if not preflight_failed:
                    cal_auth = await self._check_calendar_auth(user_id=authenticated_user_id)
                    if not cal_auth:
                        preflight_failed = True
                        preflight_error_msg = "Calendar access unauthorized"
                        preflight_error_code = "CALENDAR_UNAUTHORIZED"
                    else:
                        events = await self._list_calendar_events(
                            user_id=authenticated_user_id,
                            window_start=proposal.calendar_precondition.window_start,
                            window_end=proposal.calendar_precondition.window_end,
                            max_results=200,
                        )
                        if len(events) >= 200:
                            preflight_failed = True
                            preflight_error_msg = "Calendar saturation boundary exceeded (>=200 events)"
                            preflight_error_code = "CALENDAR_SATURATION_EXCEEDED"
                        else:
                            cal_fp = self._compute_calendar_fingerprint(events)
                            if cal_fp != proposal.calendar_precondition.calendar_event_fingerprint:
                                preflight_failed = True
                                preflight_error_msg = "Calendar schedule drifted during confirmation window"
                                preflight_error_code = "CALENDAR_DRIFT"

                # Step 9: Commitment Preflight & Authorization
                if not preflight_failed:
                    for cd in proposal.commitment_dependencies:
                        comm_auth = await self._check_commitment_auth(user_id=authenticated_user_id, commitment_id=cd.commitment_id)
                        if not comm_auth:
                            preflight_failed = True
                            preflight_error_msg = f"Unauthorized to inspect commitment {cd.commitment_id}"
                            preflight_error_code = "FORBIDDEN"
                            break
                        comm = await self._get_commitment(user_id=authenticated_user_id, commitment_id=cd.commitment_id)
                        if comm is None or comm.target_date != cd.expected_target_date:
                            preflight_failed = True
                            preflight_error_msg = f"Commitment dependency {cd.commitment_id} drifted"
                            preflight_error_code = "COMMITMENT_TARGET_DATE_DRIFT"
                            break

                # Step 10: Task Preconditions Preflight
                if not preflight_failed:
                    for tp in proposal.task_preconditions:
                        t_auth = await self._check_task_auth(user_id=authenticated_user_id, task_id=tp.task_id)
                        if not t_auth:
                            preflight_failed = True
                            preflight_error_msg = f"Unauthorized to inspect task {tp.task_id}"
                            preflight_error_code = "FORBIDDEN"
                            break
                        live_task = await self._get_task(user_id=authenticated_user_id, task_id=tp.task_id)
                        if live_task is None or getattr(live_task, "version", 1) != tp.expected_version or live_task.status != tp.expected_status:
                            preflight_failed = True
                            preflight_error_msg = f"Task precondition {tp.task_id} version or status drifted"
                            preflight_error_code = "TASK_VERSION_DRIFT"
                            break
                        if self._compute_task_state_hash(live_task) != tp.expected_state_hash:
                            preflight_failed = True
                            preflight_error_msg = f"Task {tp.task_id} state hash mismatch"
                            preflight_error_code = "TASK_STATE_HASH_MISMATCH"
                            break

                # Step 11: Reminder Preconditions Preflight
                if not preflight_failed:
                    for rp in proposal.reminder_preconditions:
                        r_auth = await self._check_reminder_auth(user_id=authenticated_user_id, reminder_id=rp.reminder_id)
                        if not r_auth:
                            preflight_failed = True
                            preflight_error_msg = f"Unauthorized to inspect reminder {rp.reminder_id}"
                            preflight_error_code = "FORBIDDEN"
                            break
                        live_rem = await self._get_reminder(user_id=authenticated_user_id, reminder_id=rp.reminder_id)
                        if live_rem is None or live_rem.scheduled_at != rp.expected_scheduled_at or live_rem.status != rp.expected_status:
                            preflight_failed = True
                            preflight_error_msg = f"Reminder precondition {rp.reminder_id} drifted"
                            preflight_error_code = "REMINDER_DRIFT"
                            break
                        if self._compute_reminder_state_hash(live_rem) != rp.expected_state_hash:
                            preflight_failed = True
                            preflight_error_msg = f"Reminder {rp.reminder_id} state hash mismatch"
                            preflight_error_code = "REMINDER_STATE_HASH_MISMATCH"
                            break

                # Step 12: Recheck Global Preconditions Hash
                if not preflight_failed:
                    current_global_hash = self._compute_preconditions_hash(
                        proposal.task_preconditions,
                        proposal.reminder_preconditions,
                        proposal.commitment_dependencies,
                        proposal.working_hours_dependency,
                        proposal.calendar_precondition,
                    )
                    if current_global_hash != proposal.preconditions_hash:
                        preflight_failed = True
                        preflight_error_msg = "Global precondition hash mismatch"
                        preflight_error_code = "GLOBAL_HASH_MISMATCH"

                # Step 13: Final Pre-Mutation Time Boundary Check (Phase 6G Zero-Await Guard)
                if not preflight_failed:
                    curr_utc = datetime.now(timezone.utc)
                    for op in proposal.operations:
                        if op.operation_type == RecoveryOperationType.RESCHEDULE_REMINDER:
                            if op.new_scheduled_time <= curr_utc:
                                preflight_failed = True
                                preflight_error_msg = f"Reminder scheduled time {op.new_scheduled_time.isoformat()} is in the past"
                                preflight_error_code = "REMINDER_TIME_LAPSED"
                                break

                # Handle Case A (Preflight Failure -> ABORTED, HTTP 412)
                if preflight_failed:
                    t_preflight_end = time.perf_counter()
                    preflight_duration_ms = (t_preflight_end - t_preflight_start) * 1000.0
                    total_duration_ms = preflight_duration_ms
                    completed_at = datetime.now(timezone.utc)

                    # Update proposal to ABORTED
                    await self._store.update_proposal_status(
                        proposal_id=proposal_id,
                        new_status=RecoveryProposalStatus.ABORTED,
                        failure_reason=preflight_error_msg,
                    )

                    diagnostics = RecoveryExecutionDiagnostics(
                        preflight_checked_at=preflight_checked_at,
                        preflight_duration_ms=preflight_duration_ms,
                        execution_duration_ms=0.0,
                        total_duration_ms=total_duration_ms,
                        presentation_notes=(),
                    )
                    result = RecoveryExecutionResult(
                        execution_id=execution_id,
                        proposal_id=proposal_id,
                        user_id=authenticated_user_id,
                        status=RecoveryExecutionStatus.FAILED,
                        started_at=preflight_checked_at,
                        completed_at=completed_at,
                        completed_operations=(),
                        failed_operation_id=None,
                        failure_reason=preflight_error_msg,
                        execution_trace=tuple(execution_trace + [f"Preflight failed: {preflight_error_msg}"]),
                        diagnostics=diagnostics,
                    )
                    await self._store.store_result(result)
                    raise PreconditionFailedError(preflight_error_msg, preflight_error_code)

                # Step 14: Finalize Preflight Duration
                t_preflight_end = time.perf_counter()
                preflight_duration_ms = (t_preflight_end - t_preflight_start) * 1000.0
                execution_trace.append(f"Preflight completed successfully in {preflight_duration_ms:.2f}ms")

                # Step 15: Freeze Proposal Global Hash
                # (Stored preconditions_hash remains immutable)

                # Steps 16–20: Sequential Mutation Dispatch
                t_exec_start = time.perf_counter()
                failed_op_id: Optional[str] = None
                mutation_failure_reason: Optional[str] = None

                for i, op in enumerate(proposal.operations):
                    op_index = i + 1
                    try:
                        if op.operation_type == RecoveryOperationType.RESCHEDULE_TASK:
                            # Payload is RescheduleTaskPayload
                            payload_task: RescheduleTaskPayload = op.payload  # type: ignore
                            update_res = await self._update_task(
                                user_id=authenticated_user_id,
                                task_id=payload_task.task_id,
                                due_at=payload_task.new_due_at,
                                expected_version=payload_task.expected_version,
                            )
                            if update_res is None:
                                raise RuntimeError(f"TaskManager update returned None for task {payload_task.task_id}")

                        elif op.operation_type == RecoveryOperationType.RESCHEDULE_REMINDER:
                            payload_rem: RescheduleReminderPayload = op.payload  # type: ignore
                            update_res = await self._update_reminder(
                                user_id=authenticated_user_id,
                                reminder_id=payload_rem.reminder_id,
                                scheduled_at=payload_rem.new_scheduled_at,
                            )
                            if update_res is None:
                                raise RuntimeError(f"ReminderManager update returned None for reminder {payload_rem.reminder_id}")

                        completed_operations.append(op.operation_id)
                        execution_trace.append(f"Operation {op_index} ({op.operation_id}) succeeded")

                    except Exception as exc:
                        failed_op_id = op.operation_id
                        mutation_failure_reason = str(exc)
                        execution_trace.append(f"Operation {op_index} ({op.operation_id}) failed: {mutation_failure_reason}")
                        break

                t_exec_end = time.perf_counter()
                execution_duration_ms = (t_exec_end - t_exec_start) * 1000.0
                total_duration_ms = preflight_duration_ms + execution_duration_ms
                completed_at = datetime.now(timezone.utc)

                # Step 21: Transition Proposal Status
                if failed_op_id is not None:
                    # Pipeline failed during mutation
                    await self._store.update_proposal_status(
                        proposal_id=proposal_id,
                        new_status=RecoveryProposalStatus.ABORTED,
                        failure_reason=mutation_failure_reason,
                    )
                    # Determine Case B vs Case C
                    if len(completed_operations) == 0:
                        # Case B: Op 1 mutation failure -> FAILED, HTTP 409
                        exec_status = RecoveryExecutionStatus.FAILED
                    else:
                        # Case C: Op K failure -> PARTIALLY_EXECUTED, HTTP 409
                        exec_status = RecoveryExecutionStatus.PARTIALLY_EXECUTED

                    diagnostics = RecoveryExecutionDiagnostics(
                        preflight_checked_at=preflight_checked_at,
                        preflight_duration_ms=preflight_duration_ms,
                        execution_duration_ms=execution_duration_ms,
                        total_duration_ms=total_duration_ms,
                        presentation_notes=(),
                    )
                    result = RecoveryExecutionResult(
                        execution_id=execution_id,
                        proposal_id=proposal_id,
                        user_id=authenticated_user_id,
                        status=exec_status,
                        started_at=preflight_checked_at,
                        completed_at=completed_at,
                        completed_operations=tuple(completed_operations),
                        failed_operation_id=failed_op_id,
                        failure_reason=mutation_failure_reason,
                        execution_trace=tuple(execution_trace),
                        diagnostics=diagnostics,
                    )
                    # Step 23: Insert Execution Result into Store
                    await self._store.store_result(result)
                    # Raise PartialExecutionError (mapped to HTTP 409 Conflict)
                    raise PartialExecutionError(
                        message=mutation_failure_reason or "Mutation failed mid-pipeline",
                        failed_operation_id=failed_op_id,
                        completed_operations=tuple(completed_operations),
                    )

                # Step 20 & 21: Case D — All Operations Succeeded -> APPLIED, HTTP 200
                await self._store.update_proposal_status(
                    proposal_id=proposal_id,
                    new_status=RecoveryProposalStatus.APPLIED,
                    applied_at=completed_at,
                )

                # Step 22: Generate Ephemeral Recovery Notes
                notes: List[EphemeralRecoveryNote] = [
                    EphemeralRecoveryNote(
                        note_id=f"note_{uuid4().hex[:8]}",
                        title="Schedule Recovery Successfully Applied",
                        details=f"All {len(completed_operations)} operations executed without conflict.",
                        affected_entities=tuple(op.target_entity_id for op in proposal.operations),
                        generated_at=completed_at,
                    )
                ]

                # Step 23: Insert Execution Result into Store
                diagnostics = RecoveryExecutionDiagnostics(
                    preflight_checked_at=preflight_checked_at,
                    preflight_duration_ms=preflight_duration_ms,
                    execution_duration_ms=execution_duration_ms,
                    total_duration_ms=total_duration_ms,
                    presentation_notes=tuple(notes),
                )
                result = RecoveryExecutionResult(
                    execution_id=execution_id,
                    proposal_id=proposal_id,
                    user_id=authenticated_user_id,
                    status=RecoveryExecutionStatus.EXECUTED,
                    started_at=preflight_checked_at,
                    completed_at=completed_at,
                    completed_operations=tuple(completed_operations),
                    failed_operation_id=None,
                    failure_reason=None,
                    execution_trace=tuple(execution_trace + ["All operations executed successfully"]),
                    diagnostics=diagnostics,
                )
                await self._store.store_result(result)

                # Step 24: Return response (locks are retired via context managers)
                return result

    # ── Helper Hash Primitives ───────────────────────────────────────────────

    def _compute_task_state_hash(self, task: Any) -> str:
        task_v = getattr(task, "version", 1)
        payload = f"{task.id}:{task_v}:{task.status}:{task.due_at}:{task.source_commitment_id}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _compute_reminder_state_hash(self, reminder: Any) -> str:
        payload = f"{reminder.id}:{reminder.scheduled_at}:{reminder.status}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _compute_calendar_fingerprint(self, events: List[Any]) -> str:
        return ScheduleRecoveryProposalBuilder._compute_calendar_fingerprint(events)

    def _compute_preconditions_hash(
        self,
        task_preconditions: Tuple[RecoveryTaskPrecondition, ...],
        reminder_preconditions: Tuple[RecoveryReminderPrecondition, ...],
        commitment_dependencies: Tuple[CommitmentExternalDependency, ...],
        working_hours_dependency: WorkingHoursExternalDependency,
        calendar_precondition: CalendarExclusivityPrecondition,
    ) -> str:
        payload = {
            "tasks": [tp.model_dump(mode="json") for tp in task_preconditions],
            "reminders": [rp.model_dump(mode="json") for rp in reminder_preconditions],
            "commitments": [cd.model_dump(mode="json") for cd in commitment_dependencies],
            "working_hours": working_hours_dependency.model_dump(mode="json"),
            "calendar": calendar_precondition.model_dump(mode="json"),
        }
        canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ==============================================================================
# 7. Dependency Injection Factory / Singletons
# ==============================================================================

_store_instance: Optional[ScheduleRecoveryExecutionStore] = None
_state_lock_manager_instance: Optional[ProposalStateLockManager] = None
_user_lock_manager_instance: Optional[UserExecutionLockManager] = None
_coordinator_instance: Optional[ProposalLifecycleCoordinator] = None


def get_schedule_recovery_execution_store() -> ScheduleRecoveryExecutionStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = ScheduleRecoveryExecutionStore()
    return _store_instance


def get_proposal_state_lock_manager() -> ProposalStateLockManager:
    global _state_lock_manager_instance
    if _state_lock_manager_instance is None:
        _state_lock_manager_instance = ProposalStateLockManager()
    return _state_lock_manager_instance


def get_user_execution_lock_manager() -> UserExecutionLockManager:
    global _user_lock_manager_instance
    if _user_lock_manager_instance is None:
        _user_lock_manager_instance = UserExecutionLockManager()
    return _user_lock_manager_instance


def get_schedule_recovery_coordinator() -> ProposalLifecycleCoordinator:
    global _coordinator_instance
    if _coordinator_instance is None:
        from app.services.tasks.manager import get_task_manager
        from app.services.reminders.manager import get_reminder_manager
        from app.services.commitments.manager import get_commitment_manager
        from app.services.calendar.manager import get_calendar_manager
        from app.services.agenda.service import get_agenda_service

        task_mgr = get_task_manager()
        rem_mgr = get_reminder_manager()
        comm_mgr = get_commitment_manager()
        cal_mgr = get_calendar_manager()
        agenda_svc = get_agenda_service()

        wh_resolver = Phase6IWorkingHoursDependencyResolver(agenda_service=agenda_svc)
        builder = ScheduleRecoveryProposalBuilder(
            task_manager=task_mgr,
            reminder_manager=rem_mgr,
            commitment_manager=comm_mgr,
            calendar_manager=cal_mgr,
            working_hours_resolver=wh_resolver,
        )

        _coordinator_instance = ProposalLifecycleCoordinator(
            store=get_schedule_recovery_execution_store(),
            state_lock_manager=get_proposal_state_lock_manager(),
            user_lock_manager=get_user_execution_lock_manager(),
            working_hours_resolver=wh_resolver,
            builder=builder,
            task_manager=task_mgr,
            reminder_manager=rem_mgr,
            commitment_manager=comm_mgr,
            calendar_manager=cal_mgr,
        )
    return _coordinator_instance


def get_recovery_coordinator_dependency() -> ProposalLifecycleCoordinator:
    return get_schedule_recovery_coordinator()
