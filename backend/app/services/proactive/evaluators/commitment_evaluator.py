"""
Phase 5I — CommitmentProposalEvaluator.

Inspects active commitments on-demand and deterministically generates OPEN_COMMITMENT proposals:
- Approaching commitment within 4 hours (now_utc <= target_date <= now_utc + 4h):
  OPEN_COMMITMENT, MEDIUM priority, COMMITMENT_DUE trigger.

Invariants:
- Evaluates strictly on-demand; no background daemon or polling.
- Does NOT generate state-changing actions (COMPLETE_COMMITMENT, etc.).
- Reuses existing CommitmentManager.
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from app.services.identity.models import DEFAULT_LOCAL_USER_ID
from app.services.commitments import CommitmentManager, get_commitment_manager, CommitmentStatus
from app.services.proactive.evaluators.base import BaseSubsystemEvaluator
from app.services.proactive.models import (
    ProactiveCandidate,
    ProactiveAction,
    ProactivePriority,
    ProactiveTrigger,
    ProactiveStatus,
)


class CommitmentProposalEvaluator(BaseSubsystemEvaluator):
    """
    On-demand evaluator for commitment-related proactive suggestions.
    """

    def __init__(self, commitment_manager: Optional[CommitmentManager] = None):
        self._commitment_manager = commitment_manager

    @property
    def commitment_manager(self) -> CommitmentManager:
        return self._commitment_manager or get_commitment_manager()

    async def evaluate_proposals(self, user_id: str = DEFAULT_LOCAL_USER_ID) -> List[ProactiveCandidate]:
        now_utc = datetime.now(timezone.utc)
        approaching_threshold = now_utc + timedelta(hours=4)

        commitments = await self.commitment_manager.list_commitments(
            user_id=user_id,
            statuses=[CommitmentStatus.ACTIVE],
            limit=50,
        )

        candidates: List[ProactiveCandidate] = []

        for cmt in commitments:
            if not cmt.target_date:
                continue

            target_utc = cmt.target_date if cmt.target_date.tzinfo else cmt.target_date.replace(tzinfo=timezone.utc)

            # Approaching commitment within next 4 hours
            if now_utc <= target_utc <= approaching_threshold:
                candidate = ProactiveCandidate(
                    user_id=user_id,
                    source="commitments",
                    trigger=ProactiveTrigger.COMMITMENT_DUE,
                    action=ProactiveAction.OPEN_COMMITMENT,
                    priority=ProactivePriority.MEDIUM,
                    status=ProactiveStatus.PENDING,
                    title=f"Upcoming commitment: {cmt.title}",
                    message=f"Commitment '{cmt.title}' is targeted for completion at {target_utc.strftime('%H:%M UTC')}.",
                    reference_id=cmt.id,
                    action_payload={"commitment_id": cmt.id},
                )
                candidates.append(candidate)

        return candidates
