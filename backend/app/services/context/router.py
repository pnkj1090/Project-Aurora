"""
Phase 6A — ContextRelevanceRouter.

Deterministic, rule-based relevance router that identifies which context domains
are potentially relevant for a given turn.

CRITICAL INVARIANTS:
1. Relevance NEVER grants permission. The security flow is:
   ContextRelevanceRouter -> ContextAccessPolicy -> Provider execution.
2. Does NOT execute LLM calls or semantic classifiers.
3. Does NOT query databases or external services.
4. Operates in-memory and deterministically on user_text and session/execution state.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from pydantic import BaseModel, Field


class RelevanceDecision(BaseModel):
    temporal: bool = Field(default=True, description="Always active")
    memory: bool = Field(default=False, description="Personal/contextual memory intent")
    calendar: bool = Field(default=False, description="Scheduling / meeting intent")
    tasks: bool = Field(default=False, description="Task / action item intent")
    commitments: bool = Field(default=False, description="Commitment / promise intent")
    reminders: bool = Field(default=False, description="Reminder / alert intent")
    location: bool = Field(default=False, description="Spatial / location intent")
    contacts: bool = Field(default=False, description="People / contact lookup intent")
    assets: bool = Field(default=False, description="File / photo intent")
    proactive: bool = Field(default=False, description="Proactive suggestion intent")
    web_sources: bool = Field(default=False, description="True only if execution_state has registered citations")


# Compiled regex patterns for fast, deterministic evaluation
_RE_FIRST_PERSON = re.compile(r"\b(i|me|my|mine|myself)\b", re.IGNORECASE)

_RE_PERSONAL_PREFERENCE = re.compile(
    r"\b(prefer|preference|preferences|usually|favorite|favourite|habit|like|dislike|"
    r"style|routine|remember|recall|know about me|remind me what|suggest for me|"
    r"recommend for me|help me plan)\b",
    re.IGNORECASE,
)

_RE_GENERAL_FACTUAL = re.compile(
    r"\b(what is|who was|who is|when was|where is|capital of|population of|define|how many)\b",
    re.IGNORECASE,
)

_RE_TECHNICAL_CODING = re.compile(
    r"\b(code|function|debug|script|syntax|algorithm|regex|sql query|class|method|"
    r"calculate|integral|formula|derivative|compiler|runtime)\b",
    re.IGNORECASE,
)

_RE_GENERAL_EXPLANATION = re.compile(
    r"\b(explain|how does|why do|difference between)\b",
    re.IGNORECASE,
)

_RE_LIFESTYLE_FALLBACK = re.compile(
    r"\b(dinner|lunch|breakfast|coffee|trip|weekend|music|movie|restaurant|food|drink|holiday|vacation)\b",
    re.IGNORECASE,
)

_RE_CALENDAR = re.compile(
    r"\b(calendar|meeting|meetings|schedule|appointment|appointments|event|events|"
    r"what's next|whats next|today|tomorrow|this week|busy|free time|available|agenda|"
    r"when is|call with|sync with)\b",
    re.IGNORECASE,
)

_RE_TASKS = re.compile(
    r"\b(task|tasks|todo|todos|to-do|to-dos|action item|action items|deadline|deadlines|"
    r"what do i have to do|what's on my plate|whats on my plate|backlog|work on)\b",
    re.IGNORECASE,
)

_RE_COMMITMENTS = re.compile(
    r"\b(commit|commitment|commitments|promise|promised|agreed to|obligation|obligations|"
    r"follow up|follow-up|i said i would|what did i promise)\b",
    re.IGNORECASE,
)

_RE_REMINDERS = re.compile(
    r"\b(remind|reminder|reminders|alert|alerts|alarm|due|notify me|upcoming reminders|remind me)\b",
    re.IGNORECASE,
)

_RE_LOCATION = re.compile(
    r"\b(where am i|location|nearby|near me|closest|around here|weather|forecast|directions|local time|traffic)\b",
    re.IGNORECASE,
)

_RE_CONTACTS = re.compile(
    r"\b(contact|contacts|call|phone|email|message|text|address of|who is|reach out to|get in touch with)\b",
    re.IGNORECASE,
)

_RE_ASSETS = re.compile(
    r"\b(photo|photos|image|images|picture|pictures|file|files|document|documents|attachment|uploaded|asset|assets)\b",
    re.IGNORECASE,
)

_RE_PROACTIVE = re.compile(
    r"\b(suggestion|suggestions|recommendation|recommendations|what should i do|"
    r"needs my attention|proactive|proposals|what should aurora remind)\b",
    re.IGNORECASE,
)


class ContextRelevanceRouter:
    """
    Evaluates turn queries and session state to determine which domains
    should be evaluated for situational context.
    """

    def evaluate(
        self,
        query: Optional[str],
        session: Optional[Any] = None,
        execution_state: Optional[Any] = None,
        **kwargs: Any,
    ) -> RelevanceDecision:
        decision = RelevanceDecision()

        clean_query = (query or "").strip()

        # Web citations: active only if execution_state has registered Phase 5K citations
        if execution_state is not None and hasattr(execution_state, "citation_registry"):
            if not execution_state.citation_registry.is_empty():
                decision.web_sources = True

        if not clean_query:
            # Query-free calls (e.g. initial greeting without prompt) only activate temporal
            return decision

        # 1. Memory Relevance (Deterministic Categories A, B, C)
        has_first_person = bool(_RE_FIRST_PERSON.search(clean_query))
        has_personal_pref = bool(_RE_PERSONAL_PREFERENCE.search(clean_query))

        if has_first_person or has_personal_pref:
            # Category A: Personal/Contextual query
            decision.memory = True
        else:
            # Check Category B: General Factual / Coding / Scientific Explanations
            is_factual = bool(_RE_GENERAL_FACTUAL.search(clean_query))
            is_technical = bool(_RE_TECHNICAL_CODING.search(clean_query))
            is_explanation = bool(_RE_GENERAL_EXPLANATION.search(clean_query))

            if is_factual or is_technical or is_explanation:
                # Category B: Suppress broad memory retrieval
                decision.memory = False
            else:
                # Category C: Ambiguous query -> check lifestyle fallback
                if bool(_RE_LIFESTYLE_FALLBACK.search(clean_query)):
                    decision.memory = True
                else:
                    decision.memory = False

        # 2. Calendar Relevance
        if bool(_RE_CALENDAR.search(clean_query)):
            decision.calendar = True

        # 3. Tasks Relevance
        if bool(_RE_TASKS.search(clean_query)):
            decision.tasks = True

        # 4. Commitments Relevance
        if bool(_RE_COMMITMENTS.search(clean_query)):
            decision.commitments = True

        # 5. Reminders Relevance
        if bool(_RE_REMINDERS.search(clean_query)):
            decision.reminders = True

        # 6. Location Relevance
        if bool(_RE_LOCATION.search(clean_query)):
            decision.location = True
        elif session and hasattr(session, "metadata") and isinstance(session.metadata, dict):
            if session.metadata.get("location_active"):
                decision.location = True

        # 7. Contacts Relevance
        if kwargs.get("contact_query") or bool(_RE_CONTACTS.search(clean_query)):
            decision.contacts = True

        # 8. Assets Relevance
        if kwargs.get("asset_query") or bool(_RE_ASSETS.search(clean_query)):
            decision.assets = True
        elif session and hasattr(session, "metadata") and isinstance(session.metadata, dict):
            if session.metadata.get("active_assets"):
                decision.assets = True

        # 9. Proactive Suggestions Relevance
        if kwargs.get("proactive_query") or bool(_RE_PROACTIVE.search(clean_query)):
            decision.proactive = True

        return decision
