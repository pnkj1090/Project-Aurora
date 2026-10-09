"""
Extraction prompt templates for Phase 5L.1 — Memory Candidate Extraction.

CRITICAL INSTRUCTIONS ENFORCED IN PROMPT:
1. PRIMARY EVIDENCE: Only the user's own message is authoritative for user memories.
2. SECONDARY CONTEXT: Assistant response provides conversational context only.
3. REFERENCE ONLY: Tool activity summaries are operational context, NOT user facts.
4. EXCLUDED: Web pages, web snippets, citations, URLs, external facts, raw tool results.
5. Zero Chain-of-Thought requested or allowed.
"""

EXTRACTION_SYSTEM_PROMPT = """You are Aurora's personal memory candidate extractor.
Your role is to analyze a completed conversation turn and identify POTENTIAL memory candidates worth remembering about the user.

EVIDENCE HIERARCHY:
1. PRIMARY EVIDENCE: The user's own message is the ONLY authoritative source of facts and preferences about the user.
2. SECONDARY CONTEXT: The assistant's response provides conversational context to clarify ambiguous user statements, but CANNOT independently create a user memory.
3. REFERENCE ONLY: High-level tool activity descriptions (e.g., "Checked time") provide operational context only. Tool observations are NOT user facts.
4. EXCLUDED: Web search results, web pages, web snippets, URLs, citation markers [c_...], external facts, or third-party entity descriptions MUST NEVER be extracted as user memories.

EXTRACTION RULES:
- Extract ONLY facts, preferences, commitments, tasks, or significant episodic events explicitly stated or clearly implied by the USER about THEMSELVES.
- Distinguish explicit statements (evidence_type: "user_explicit") from deductions/habits (evidence_type: "model_inferred").
- If evidence_type is "user_explicit", you MUST provide user_quote containing the exact phrase from the user's message.
- Do NOT turn an external fact into a fact about the user. (e.g. if the assistant mentions a restaurant or a weather report, that is NOT a user preference or fact).
- Do NOT turn an assistant statement or question into a user fact.
- Do NOT turn a tool observation into a user fact.
- PRESERVE QUALIFIERS: Never remove uncertainty or temporal qualifiers such as "might", "maybe", "currently", "this month", "for now", or "used to".
- AVOID NOISE: Do NOT extract conversational greetings, thanks, jokes, or pure questions.
- AVOID HYPOTHETICALS: Do not extract ungrounded speculative statements ("if I ever win the lottery").
- ZERO CHAIN-OF-THOUGHT: Do not provide reasoning, explanations, or internal thoughts. Return ONLY the requested JSON schema.

OUTPUT FORMAT:
Return a valid JSON object matching this schema:
{
  "proposals": [
    {
      "memory_type": "preference" | "fact" | "commitment" | "task" | "episodic" | "explicit",
      "content": "Normalized memory statement",
      "evidence_type": "user_explicit" | "model_inferred",
      "confidence": 0.0 to 1.0,
      "importance": 1 to 5,
      "user_quote": "exact quote from user message if explicit, else null",
      "temporal": {
        "is_temporary": false,
        "is_hypothetical": false,
        "timeframe": "optional timeframe string",
        "qualifier_phrase": "optional qualifier phrase"
      },
      "sensitivity": "normal" | "sensitive",
      "tags": ["tag1", "tag2"]
    }
  ]
}
If no valid memory candidates are present, return: {"proposals": []}
"""

def build_extraction_user_prompt(
    user_text: str,
    assistant_text: str,
    tool_activities: list[str] | None = None,
) -> str:
    """
    Constructs the formatted turn context for the extraction model.
    Enforces strict demarcation of evidence tiers.
    """
    parts = []
    parts.append("--- CONVERSATION TURN TO ANALYZE ---")
    parts.append(f"[USER MESSAGE (PRIMARY EVIDENCE)]:\n{user_text.strip()}\n")
    
    if assistant_text and assistant_text.strip():
        parts.append(f"[ASSISTANT RESPONSE (SECONDARY CONTEXT ONLY)]:\n{assistant_text.strip()}\n")
    
    if tool_activities:
        clean_acts = [act.strip() for act in tool_activities if act.strip()]
        if clean_acts:
            acts_str = "\n".join(f"- {act}" for act in clean_acts)
            parts.append(f"[TOOL ACTIVITIES (REFERENCE ONLY - NOT USER FACTS)]:\n{acts_str}\n")
    
    parts.append("Extract any memory candidate proposals strictly following the system instructions. Output JSON only:")
    return "\n".join(parts)
