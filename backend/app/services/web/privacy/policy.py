"""
Privacy policy and prohibited data pattern definitions for Project Aurora.
Defines all signatures of internal IDs, credentials, PII, and private contexts.
"""
import re
from typing import List, Pattern

# Regular expressions for prohibited internal identifiers and entity structures
PROHIBITED_ID_PATTERNS: List[Pattern] = [
    # Standard UUID format
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    # Aurora internal entity IDs: task_..., tsk_..., cmt_..., rem_..., mem_..., cand_..., conv_..., exec_..., usr_..., dev_...
    re.compile(r"\b(task|tsk|commitment|cmt|reminder|rem|memory|mem|candidate|cand|conversation|conv|execution|exec|usr|user|dev|device|asset|sess|inst)_[a-zA-Z0-9_-]{4,}\b", re.IGNORECASE),
    # Raw 32/64 char hex hashes (MD5, SHA1, SHA256)
    re.compile(r"\b[0-9a-fA-F]{32}\b"),
    re.compile(r"\b[0-9a-fA-F]{64}\b"),
]

# High-precision GPS coordinates (e.g. 28.4595, 77.0266)
GPS_COORDINATES_PATTERN: Pattern = re.compile(
    r"\b[-+]?([1-8]?\d\.\d{4,}|90\.0{4,}),\s*[-+]?(180\.0{4,}|((1[0-7]\d)|([1-9]?\d))\.\d{4,})\b"
)

# Coarser GPS coordinate pattern (e.g. 28.45, 77.02 with 2-3 decimals)
COARSE_GPS_COORDINATES_PATTERN: Pattern = re.compile(
    r"\b[-+]?([1-8]?\d\.\d{2,}|90\.0{2,}),\s*[-+]?(180\.0{2,}|((1[0-7]\d)|([1-9]?\d))\.\d{2,})\b"
)

# Personal Identifiable Information (PII) patterns
EMAIL_PATTERN: Pattern = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
PHONE_PATTERN: Pattern = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")

# Credentials, tokens, keys, and authorization headers
CREDENTIAL_PATTERNS: List[Pattern] = [
    # Bearer tokens
    re.compile(r"\bBearer\s+[a-zA-Z0-9._~+/-]+=*", re.IGNORECASE),
    # OpenAI, Google, GitHub API keys
    re.compile(r"\bsk-[a-zA-Z0-9_-]{5,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z-_]{30,45}\b"),
    re.compile(r"\bghp_[a-zA-Z0-9]{20,}\b"),
    # General secret key assignments
    re.compile(r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|passwd|password)\s*[:=]\s*[^\s]+", re.IGNORECASE),
    # Cookie headers
    re.compile(r"\bcookie\s*:\s*[^\s]+", re.IGNORECASE),
    re.compile(r"\bauthorization\s*:\s*[^\s]+", re.IGNORECASE),
]

# LAN, private IP addresses, and intranet hostnames
PRIVATE_NETWORK_PATTERNS: List[Pattern] = [
    re.compile(r"\b(localhost|127\.0\.0\.1|0\.0\.0\.0|169\.254\.169\.254)\b", re.IGNORECASE),
    re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
    re.compile(r"\b[a-zA-Z0-9-]+\.(local|internal|lan|corp|home|intranet)\b", re.IGNORECASE),
    re.compile(r"https?://(localhost|127\.0\.0\.1|192\.168\.|10\.|172\.)", re.IGNORECASE),
]

# System/developer prompt leak patterns
PROMPT_INJECTION_OR_LEAK_PATTERNS: List[Pattern] = [
    re.compile(r"<untrusted_web_content", re.IGNORECASE),
    re.compile(r"\b(ignore\s+all\s+previous\s+instructions|ignore\s+prior\s+instructions)\b", re.IGNORECASE),
    re.compile(r"\b(system\s*:\s*|developer\s*:\s*|assistant\s*:\s*|human\s*:\s*)\b", re.IGNORECASE),
]
