from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.services.identity.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.services.agenda.schedule_recovery import (
    ProposalLifecycleCoordinator,
    get_recovery_coordinator_dependency,
)
from app.services.agenda.schedule_recovery_models import (
    ActiveExecutionLockConflictError,
    ApplyRecoveryProposalRequest,
    AuthorizationPrecedenceError,
    CancelRecoveryProposalRequest,
    EvaluateRecoveryProposalRequest,
    ExecutionResultCapacityExceededError,
    PartialExecutionError,
    Phase6IError,
    PreconditionFailedError,
    ProposalAlreadyTerminalError,
    ProposalCapacityExceededError,
    ProposalExpiredError,
    ProposalNotFoundError,
    ProposalStateConflictError,
    ProposeScheduleRecoveryRequest,
    RecoveryEvaluationResult,
    RecoveryExecutionResult,
    ResultNotFoundError,
    ScheduleRecoveryProposal,
)

from app.services.agenda.schedule_disruption import (
    ScheduleDisruptionDiagnosticService,
    get_diagnostic_service_dependency,
)
from app.services.agenda.schedule_disruption_models import (
    CalendarStateError,
    DiagnoseScheduleDisruptionRequest,
    DiscoveryUniverseSaturatedError,
    InvalidTimezoneError,
    InvalidWindowError,
    Phase6JError,
    ScheduleDisruptionDiagnostic,
    TaskDomainUnavailableError,
    WorkingHoursUnresolvableError,
)

logger = logging.getLogger("aurora.api.schedule_recovery")
router = APIRouter()


# ==============================================================================
# Endpoint 1: Propose Schedule Recovery (POST /propose -> HTTP 201)
# ==============================================================================

@router.post(
    "/propose",
    response_model=ScheduleRecoveryProposal,
    status_code=status.HTTP_201_CREATED,
    summary="Propose Schedule Recovery",
)
async def propose_schedule_recovery(
    request: ProposeScheduleRecoveryRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordinator: ProposalLifecycleCoordinator = Depends(get_recovery_coordinator_dependency),
) -> ScheduleRecoveryProposal:
    try:
        user_id = principal.user.user_id
        proposal = await coordinator.propose_schedule_recovery(user_id=user_id, request=request)
        return proposal
    except AuthorizationPrecedenceError as ape:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "FORBIDDEN", "message": str(ape)})
    except PreconditionFailedError as pfe:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail={"error": pfe.error_code, "message": str(pfe)})
    except ProposalCapacityExceededError as pce:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": pce.error_code, "message": str(pce)})
    except Phase6IError as p6e:
        if p6e.error_code in ("TASK_NOT_FOUND", "REMINDER_NOT_FOUND", "COMMITMENT_NOT_FOUND"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": p6e.error_code, "message": str(p6e)})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": p6e.error_code, "message": str(p6e)})
    except Exception as exc:
        logger.exception("Unexpected error during proposal creation")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "INTERNAL_ERROR", "message": str(exc)})


# ==============================================================================
# Endpoint 2: Get Proposal Snapshot (GET /proposals/{proposal_id} -> HTTP 200)
# ==============================================================================

@router.get(
    "/proposals/{proposal_id}",
    response_model=ScheduleRecoveryProposal,
    status_code=status.HTTP_200_OK,
    summary="Get Proposal Snapshot",
)
async def get_proposal_snapshot(
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordinator: ProposalLifecycleCoordinator = Depends(get_recovery_coordinator_dependency),
) -> ScheduleRecoveryProposal:
    try:
        user_id = principal.user.user_id
        return await coordinator.get_proposal(proposal_id=proposal_id, authenticated_user_id=user_id)
    except ProposalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "PROPOSAL_NOT_FOUND", "message": f"Proposal {proposal_id} not found"})
    except AuthorizationPrecedenceError as ape:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "FORBIDDEN", "message": str(ape)})
    except Exception as exc:
        logger.exception("Unexpected error getting proposal snapshot")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "INTERNAL_ERROR", "message": str(exc)})


# ==============================================================================
# Endpoint 3: Cancel Proposal (POST /proposals/{proposal_id}/cancel -> HTTP 200)
# ==============================================================================

@router.post(
    "/proposals/{proposal_id}/cancel",
    response_model=ScheduleRecoveryProposal,
    status_code=status.HTTP_200_OK,
    summary="Cancel Proposal",
)
async def cancel_proposal(
    proposal_id: str,
    request: Optional[CancelRecoveryProposalRequest] = None,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordinator: ProposalLifecycleCoordinator = Depends(get_recovery_coordinator_dependency),
) -> ScheduleRecoveryProposal:
    try:
        user_id = principal.user.user_id
        reason = request.reason if request else None
        return await coordinator.cancel_proposal(proposal_id=proposal_id, authenticated_user_id=user_id, reason=reason)
    except ProposalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "PROPOSAL_NOT_FOUND", "message": f"Proposal {proposal_id} not found"})
    except AuthorizationPrecedenceError as ape:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "FORBIDDEN", "message": str(ape)})
    except ProposalExpiredError as pee:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "PROPOSAL_EXPIRED", "message": str(pee)})
    except ProposalAlreadyTerminalError as pate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "PROPOSAL_ALREADY_TERMINAL", "message": str(pate)})
    except ProposalStateConflictError as psce:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "INVALID_STATE_TRANSITION", "message": str(psce)})
    except Exception as exc:
        logger.exception("Unexpected error cancelling proposal")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "INTERNAL_ERROR", "message": str(exc)})


# ==============================================================================
# Endpoint 4: Apply Recovery Proposal (POST /proposals/{proposal_id}/apply -> HTTP 200)
# ==============================================================================

@router.post(
    "/proposals/{proposal_id}/apply",
    response_model=RecoveryExecutionResult,
    status_code=status.HTTP_200_OK,
    summary="Apply Recovery Proposal",
)
async def apply_recovery_proposal(
    proposal_id: str,
    request: ApplyRecoveryProposalRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordinator: ProposalLifecycleCoordinator = Depends(get_recovery_coordinator_dependency),
) -> RecoveryExecutionResult:
    try:
        user_id = principal.user.user_id
        return await coordinator.apply_proposal(proposal_id=proposal_id, authenticated_user_id=user_id, request=request)
    except ProposalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "PROPOSAL_NOT_FOUND", "message": f"Proposal {proposal_id} not found"})
    except AuthorizationPrecedenceError as ape:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "FORBIDDEN", "message": str(ape)})
    except ActiveExecutionLockConflictError as aelce:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "ACTIVE_EXECUTION_LOCK_CONFLICT", "message": str(aelce)})
    except ProposalExpiredError as pee:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "PROPOSAL_EXPIRED", "message": str(pee)})
    except ProposalAlreadyTerminalError as pate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "PROPOSAL_ALREADY_TERMINAL", "message": str(pate)})
    except PartialExecutionError as pee:
        # Cases B and C: mutation failures return HTTP 409 Conflict
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "PARTIAL_EXECUTION_FAILURE",
                "message": str(pee),
                "failed_operation_id": pee.failed_operation_id,
                "completed_operations": list(pee.completed_operations),
            },
        )
    except PreconditionFailedError as pfe:
        # Case A: preflight failure returns HTTP 412 Precondition Failed
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail={"error": pfe.error_code, "message": str(pfe)})
    except ExecutionResultCapacityExceededError as erce:
        raise HTTPException(status_code=status.HTTP_507_INSUFFICIENT_STORAGE, detail={"error": "EXECUTION_RESULT_CAPACITY_EXCEEDED", "message": str(erce)})
    except Phase6IError as p6e:
        if p6e.error_code == "CONFIRMATION_REQUIRED":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": "CONFIRMATION_REQUIRED", "message": str(p6e)})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": p6e.error_code, "message": str(p6e)})
    except Exception as exc:
        logger.exception("Unexpected error applying proposal")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "INTERNAL_ERROR", "message": str(exc)})


# ==============================================================================
# Endpoint 5: Get Execution Result (GET /proposals/{proposal_id}/result -> HTTP 200)
# ==============================================================================

@router.get(
    "/proposals/{proposal_id}/result",
    response_model=RecoveryExecutionResult,
    status_code=status.HTTP_200_OK,
    summary="Get Execution Result",
)
async def get_execution_result(
    proposal_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordinator: ProposalLifecycleCoordinator = Depends(get_recovery_coordinator_dependency),
) -> RecoveryExecutionResult:
    try:
        user_id = principal.user.user_id
        return await coordinator.get_execution_result(proposal_id=proposal_id, authenticated_user_id=user_id)
    except ResultNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "RESULT_NOT_FOUND", "message": f"Result for proposal {proposal_id} not found"})
    except AuthorizationPrecedenceError as ape:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "FORBIDDEN", "message": str(ape)})
    except Exception as exc:
        logger.exception("Unexpected error getting execution result")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "INTERNAL_ERROR", "message": str(exc)})


# ==============================================================================
# Endpoint 6: Evaluate Proposal (POST /evaluate -> HTTP 200)
# ==============================================================================

@router.post(
    "/evaluate",
    response_model=RecoveryEvaluationResult,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Proposal (Dry-Run Preflight)",
)
async def evaluate_proposal(
    request: EvaluateRecoveryProposalRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    coordinator: ProposalLifecycleCoordinator = Depends(get_recovery_coordinator_dependency),
) -> RecoveryEvaluationResult:
    try:
        user_id = principal.user.user_id
        return await coordinator.evaluate_proposal(proposal_id=request.proposal_id, authenticated_user_id=user_id)
    except ProposalNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"error": "PROPOSAL_NOT_FOUND", "message": f"Proposal {request.proposal_id} not found"})
    except AuthorizationPrecedenceError as ape:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"error": "FORBIDDEN", "message": str(ape)})
    except ProposalExpiredError as pee:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "PROPOSAL_EXPIRED", "message": str(pee)})
    except ProposalAlreadyTerminalError as pate:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "PROPOSAL_ALREADY_TERMINAL", "message": str(pate)})
    except Phase6IError as p6e:
        if p6e.error_code in ("PROPOSAL_ALREADY_TERMINAL", "PROPOSAL_EXPIRED"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": p6e.error_code, "message": str(p6e)})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"error": p6e.error_code, "message": str(p6e)})
    except Exception as exc:
        logger.exception("Unexpected error evaluating proposal")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "INTERNAL_ERROR", "message": str(exc)})


# ==============================================================================
# Endpoint 7: Diagnose Schedule Disruption (POST /diagnose -> HTTP 200)
# ==============================================================================

@router.post(
    "/diagnose",
    response_model=ScheduleDisruptionDiagnostic,
    status_code=status.HTTP_200_OK,
    summary="Diagnose Schedule Disruption & Discover Recovery Candidates",
)
async def diagnose_schedule_disruption(
    request: DiagnoseScheduleDisruptionRequest,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    service: ScheduleDisruptionDiagnosticService = Depends(get_diagnostic_service_dependency),
) -> ScheduleDisruptionDiagnostic:
    try:
        user_id = principal.user.user_id
        return await service.diagnose_disruption(authenticated_user_id=user_id, request=request)
    except TaskDomainUnavailableError as tde:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"error": tde.error_code, "message": str(tde)},
        )
    except WorkingHoursUnresolvableError as whe:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"error": whe.error_code, "message": str(whe)},
        )
    except CalendarStateError as cse:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"error": cse.error_code, "message": str(cse)},
        )
    except DiscoveryUniverseSaturatedError as dse:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"error": dse.error_code, "message": str(dse)},
        )
    except InvalidWindowError as iwe:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": iwe.error_code, "message": str(iwe)},
        )
    except InvalidTimezoneError as ite:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": ite.error_code, "message": str(ite)},
        )
    except Phase6JError as p6j:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": p6j.error_code, "message": str(p6j)},
        )
    except Exception as exc:
        logger.exception("Unexpected error diagnosing schedule disruption")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "INTERNAL_ERROR", "message": str(exc)},
        )
