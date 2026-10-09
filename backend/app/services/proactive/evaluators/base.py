"""
Phase 5I — Base Subsystem Evaluator.

Core Invariants:
- On-demand evaluation only.
- Zero background execution, zero daemon threads, zero continuous scanning/polling.
- Evaluators inspect domain state and produce candidate proposals.
- Every candidate must be passed through ProactiveManager.create_candidate policy pipeline.
- No evaluator may write directly to the proactive store.
"""
from abc import ABC, abstractmethod
from typing import List

from app.services.proactive.models import ProactiveCandidate


class BaseSubsystemEvaluator(ABC):
    """
    Abstract contract for on-demand domain proposal evaluators.
    """

    @abstractmethod
    async def evaluate_proposals(self, user_id: str) -> List[ProactiveCandidate]:
        """
        Inspect domain state for user_id on-demand and produce raw proactive candidate proposals.
        """
        pass
