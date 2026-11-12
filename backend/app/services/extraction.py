"""Conservative, deterministic field extraction for the intake conversation.

The vendor inference endpoint that was going to do schema-constrained LLM
extraction (Evon) is not usable in this project, so extraction is deterministic.
It strips conversational framing only (``"mera naam ... hai"`` -> the name) and
always offers the applicant's untouched utterance as the last candidate. It can
therefore only ever be *less* specific than what the applicant said, never
invented. Every candidate is still passed through ``validate_field``.

Neither strategy assigns confidence. Whatever is returned is stored as an
applicant statement that a human must review.
"""

import re

_NAME_CUES = (
    r"(?:mera|meri|hamara|hamari|मेरा|मेरी|माझे|माझं|माझा)\s+(?:poora\s+|पूर्ण\s+)?naam\s+(?:hai\s+)?",
    r"(?:mera|meri|hamara|hamari)\s+(?:पूरा\s+)?नाम\s+(?:है\s+)?",
    r"\bmy\s+name\s+is\s+",
    r"\bname\s+is\s+",
    r"\bnaam\s+(?:hai\s+)?",
    r"\bनाव\s+",
)
_NAME_SUFFIX = re.compile(r"^(.+?)\s+(?:naam|नाव)\s+(?:hai|आहे)\s*$", re.IGNORECASE)
_NAME_CLAUSE_CUT = re.compile(r"\s+(?:aur|and|आणि|aani|जो|jo)\s+.*$", re.IGNORECASE)
_NAME_PARTICLE = re.compile(r"\s+(?:hai|hain|hoon|hu|है|हैं|आहे|आहेत|आहे\.)\s*$", re.IGNORECASE)

_FATHER_CUES = (
    r"(?:mere|mera|माझ्या|माझे)\s+(?:pitaji|pita|father|वडिलांचे|वडिलांना)\s+(?:ka|ki|चे)?\s*(?:naam|नाव)\s+(?:hai|आहे|है)?\s*",
    r"(?:pitaji|pita)\s+ka\s+naam\s+(?:hai\s+)?",
    r"father'?s?\s+name\s+is\s+",
    r"father\s+name\s+is\s+",
)

_OCCUPATION_CUES = (
    (r"(?:main|mai|mein)\s+(?:ek\s+|एक\s+)?", r"\b(?:karta|karti|karte|kaam|hoon|hu|hai|hain)\b"),
    (r"मी\s+", r"(?:करतो|करते|काम|आहे)"),
    (r"i\s+(?:am|work\s+as)\s+(?:a\s+|an\s+)?", None),
    (r"my\s+occupation\s+is\s+", None),
    (r"माझा\s+व्यवसाय\s+", None),
)
_OCCUPATION_CUT = re.compile(
    # Lookahead instead of \b: Devanagari vowel signs are combining marks and do
    # not form a word boundary against punctuation.
    r"\s+(?:karta|karti|karte|karat|kaam|करतो|करते|करता|काम)(?=[\s.,!?।;:]|$).*$",
    re.IGNORECASE,
)
_OCCUPATION_PARTICLE = re.compile(r"\s+(?:hoon|hu|hai|hain|आहे|आहेत)\s*$", re.IGNORECASE)

_ADDRESS_CUES = (
    r"(?:mera|meri)\s+(?:address|pata|पत्ता)\s+(?:hai\s+)?",
    r"my\s+address\s+is\s+",
    r"i\s+(?:live|stay)\s+(?:in|at)\s+",
    r"(?:main|मी)\s+(.+?)\s+(?:mein|मध्ये)\s+(?:rehta|rehti|rahta|rahata|राहतो|राहते)\s+(?:hoon|hu|आहे)?",
)
_ADDRESS_PARTICLE = re.compile(r"\s+(?:hai|hain|hoon|hu|है|आहे)\s*$", re.IGNORECASE)


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" \t\n,.!?;:।\"'")
    return text.strip()


def _fragment_after(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    fragment = match.group(1) if match.groups() else text[match.end():]
    return _clean(fragment) or None


def local_candidates(field: str, text: str) -> list[str]:
    """Return [best-effort rewritten value, ..., original utterance].

    The caller validates them in order and keeps the first that passes, so a
    failed rewrite degrades to the applicant's exact words.
    """
    candidates: list[str] = []
    if field in ("full_name", "father_name"):
        cues = _NAME_CUES if field == "full_name" else _FATHER_CUES
        if field == "father_name" and (match := _NAME_SUFFIX.search(text)):
            candidates.append(match.group(1))
        for cue in cues:
            fragment = _fragment_after(text, cue)
            if fragment:
                candidates.append(fragment)
        cleaned = []
        for candidate in candidates:
            candidate = _NAME_CLAUSE_CUT.sub("", candidate)
            candidate = _NAME_PARTICLE.sub("", candidate)
            cleaned.append(_clean(candidate))
        candidates = cleaned
    elif field == "occupation":
        for cue, required in _OCCUPATION_CUES:
            if required and not re.search(required, text, re.IGNORECASE):
                continue
            fragment = _fragment_after(text, cue)
            if fragment:
                fragment = _OCCUPATION_CUT.sub("", fragment)
                fragment = _OCCUPATION_PARTICLE.sub("", fragment)
                candidates.append(fragment)
    elif field == "address":
        for cue in _ADDRESS_CUES:
            fragment = _fragment_after(text, cue)
            if fragment:
                candidates.append(_ADDRESS_PARTICLE.sub("", fragment))

    ordered: list[str] = []
    for candidate in candidates:
        candidate = _clean(candidate)
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    # Always keep the applicant's untouched words as the last resort, so a bad
    # rewrite can only be less specific than what was actually said.
    original = text.strip()
    if original and original not in ordered:
        ordered.append(original)
    return ordered or [text]


def clarification_attempt(messages: list, language: str, field: str) -> int:
    """How many clarification rounds this field has already had.

    Derived from the transcript rather than stored, so the schema needs no new
    column: the first prompt for a field counts as attempt 0.
    """
    from app.services.intake import QUESTIONS

    question = QUESTIONS.get(language, QUESTIONS["hinglish"]).get(field)
    if not question:
        return 0
    asked = sum(
        1
        for message in messages
        if getattr(message, "role", None) == "assistant" and question in (getattr(message, "content", "") or "")
    )
    return max(0, asked - 1)
