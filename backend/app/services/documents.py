"""Identity-document parsing and voice/document cross-checks.

Scope, stated honestly:

* PAN is checked for the documented shape only.
* Aadhaar is checked for 12 digits and a valid Verhoeff checksum.
* Name and date of birth are compared against the applicant's spoken fields.
* A result is "consistent" or "inconsistent" — never "verified identity".

No confidence score is produced, and a mismatch is only a review flag for a
human agent.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from app.services.intake import normalize_indic_digits, parse_date_of_birth

PAN_PATTERN = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
AADHAAR_PATTERN = re.compile(r"(?<!\d)(\d{4})\s*(\d{4})\s*(\d{4})(?!\d)")
DOB_PATTERN = re.compile(
    r"(?:dob|d\.o\.b|date\s+of\s+birth|जन्म\s*तारीख|जन्मतारीख|जन्म\s*दिनांक)\s*[:\-]?\s*"
    r"(\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})",
    re.IGNORECASE,
)
DATE_ANYWHERE = re.compile(
    r"(\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
)
NAME_PATTERN = re.compile(
    r"(?:name|full\s+name|नाम|पूर्ण\s+नाव|नाव)\s*[:\-]\s*([^\n\r]{2,80})",
    re.IGNORECASE,
)
TITLES = {"mr", "mrs", "ms", "shri", "smt", "dr", "sri", "श्री", "श्रीमती", "कुमारी"}

DOC_TYPES = ("pan", "aadhaar")

# Verhoeff checksum tables (Aadhaar's check digit).
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(number: str) -> bool:
    digits = normalize_indic_digits(re.sub(r"\D", "", number))
    if not digits:
        return False
    checksum = 0
    for index, char in enumerate(reversed(digits)):
        checksum = _D[checksum][_P[index % 8][int(char)]]
    return checksum == 0


def verhoeff_check_digit(number_without_check: str) -> str:
    checksum = 0
    for index, char in enumerate(reversed(number_without_check)):
        checksum = _D[checksum][_P[(index + 1) % 8][int(char)]]
    return str(_INV[checksum])


def pan_valid(value: str) -> bool:
    """Check the canonical printed PAN shape (uppercase letters/digits).

    Applicant-entered text is normalised to uppercase before extraction, but a
    validator should not silently accept a value that is not a printed PAN.
    """
    return bool(PAN_PATTERN.fullmatch((value or "").strip()))


def aadhaar_valid(value: str) -> bool:
    digits = normalize_indic_digits(re.sub(r"\D", "", value))
    if len(digits) != 12 or digits[0] not in "23456789":
        return False
    return verhoeff_valid(digits)


def mask_number(value: str) -> str:
    digits = re.sub(r"\s", "", value)
    if len(digits) <= 4:
        return "X" * len(digits)
    return f"{'X' * (len(digits) - 4)}{digits[-4:]}"


def extract_fields(text: str, doc_type: str) -> dict[str, Any]:
    """Extract only what is on the document text. Empty means not found."""
    raw = text or ""
    normalised = normalize_indic_digits(raw)
    fields: dict[str, Any] = {}

    if doc_type == "pan":
        match = PAN_PATTERN.search(normalised.upper())
        if match:
            fields["document_number"] = match.group(0)
            fields["number_masked"] = mask_number(match.group(0))
            fields["number_valid"] = True
    elif doc_type == "aadhaar":
        match = AADHAAR_PATTERN.search(normalised)
        if match:
            number = "".join(match.groups())
            fields["document_number"] = number
            fields["number_masked"] = "XXXX XXXX " + number[-4:]
            fields["number_valid"] = aadhaar_valid(number)

    name_match = NAME_PATTERN.search(normalised)
    if name_match:
        name = name_match.group(1).strip(" :-\t")
        # Cut a following labelled field on the same line, e.g. "Name: A DOB: ..."
        name = re.split(r"\s{2,}(?:dob|date|father|gender|पिता|जन्म)", name, flags=re.IGNORECASE)[0]
        if name:
            fields["full_name"] = name.strip()
    else:
        for line in normalised.splitlines():
            candidate = line.strip()
            words = [word for word in re.findall(r"[A-Za-z\u0900-\u097F]+", candidate)]
            if len(words) >= 2 and len(candidate) <= 80 and not re.search(r"\d{4}", candidate):
                fields["full_name"] = candidate
                break

    dob_match = DOB_PATTERN.search(normalised) or DATE_ANYWHERE.search(normalised)
    if dob_match:
        fields["date_of_birth"] = dob_match.group(1).strip()

    return fields


def _name_tokens(value: str) -> list[str]:
    """Ordered name tokens, lowercased, with titles and duplicates removed."""
    cleaned = re.sub(r"[^\w\u0900-\u097F\s]", " ", value.lower())
    tokens: list[str] = []
    for token in cleaned.split():
        if token and token not in TITLES and token not in tokens:
            tokens.append(token)
    return tokens


def _token_matches(left: str, right: str) -> bool:
    if left == right:
        return True
    # A single-letter token can stand for a name it starts (`k.` -> `kumar`).
    if len(left) == 1 and right.startswith(left):
        return True
    if len(right) == 1 and left.startswith(right):
        return True
    if min(len(left), len(right)) < 4:
        return False
    if left.startswith(right) or right.startswith(left):
        return True
    # Tolerate spelling/transliteration drift, but not a different given name.
    return SequenceMatcher(None, left, right).ratio() >= 0.9


def compare_names(spoken: str | None, document: str | None) -> dict[str, Any]:
    """Compare names token-wise. A shared surname alone is not a match.

    The given name must agree in one of the common orders (given-first or
    surname-first), and every token on the shorter side must have a counterpart.
    This still only produces ``consistent``/``inconsistent``: it never verifies
    identity.
    """
    left, right = _name_tokens(spoken or ""), _name_tokens(document or "")
    result: dict[str, Any] = {"field": "full_name", "spoken": spoken, "document": document}
    if not left or not right:
        return {**result, "status": "not_checked", "detail": "Name is missing on one side."}

    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    matched = sum(
        1 for token in shorter if any(_token_matches(token, candidate) for candidate in longer)
    )
    coverage = matched / len(shorter)
    # Require the given name to agree, in either common name order. Without this
    # "Ramesh Kumar" and "Suresh Kumar" would look consistent on the surname.
    given_ok = (
        _token_matches(left[0], right[0])
        or _token_matches(left[0], right[-1])
        or _token_matches(left[-1], right[0])
    )
    similarity = SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()
    consistent = coverage >= 0.99 and given_ok
    return {
        **result,
        "status": "consistent" if consistent else "inconsistent",
        "overlap": round(coverage, 2),
        "similarity": round(similarity, 2),
    }


def compare_dob(spoken: str | None, document: str | None) -> dict[str, Any]:
    left, right = parse_date_of_birth(spoken or ""), parse_date_of_birth(document or "")
    result: dict[str, Any] = {"field": "date_of_birth", "spoken": spoken, "document": document}
    if not left or not right:
        return {**result, "status": "not_checked", "detail": "Date of birth is missing on one side."}
    return {**result, "status": "consistent" if left == right else "inconsistent"}


def cross_check(extracted: dict[str, Any], document_fields: dict[str, Any]) -> dict[str, Any]:
    spoken_name = (extracted.get("full_name") or {}).get("value")
    spoken_dob = (extracted.get("date_of_birth") or {}).get("value")
    checks = [
        compare_names(spoken_name, document_fields.get("full_name")),
        compare_dob(spoken_dob, document_fields.get("date_of_birth")),
    ]
    statuses = {check["status"] for check in checks}
    if "inconsistent" in statuses:
        overall = "inconsistent"
    elif "consistent" in statuses:
        overall = "consistent"
    else:
        overall = "not_checked"
    mismatches = [check for check in checks if check["status"] == "inconsistent"]
    return {
        "overall": overall,
        "review_flag": overall == "inconsistent",
        "checks": checks,
        "mismatches": mismatches,
        "note": (
            "Cross-check only. Consistent fields are not identity verification; "
            "an authorised agent must review the original document."
        ),
    }
