"""Transparent, rule-based evidence scorecard with reason codes.

This is not credit scoring, underwriting, or an approval. It scores how much
*reviewable evidence* the application contains so an agent can prioritise work.
Every point is traceable to a reason code. The only recommendations it emits are
"standard_manual_review" and "priority_manual_review".
"""

from __future__ import annotations

from typing import Any

from app.services.intake import REQUIRED_FIELDS, parse_indian_amount

OCCUPATION_STABILITY = {
    "farmer": "Agriculture",
    "kheti": "Agriculture",
    "शेती": "Agriculture",
    "shop": "Retail",
    "dukan": "Retail",
    "दुकान": "Retail",
    "driver": "Transport",
    "चालक": "Transport",
    "tailor": "Textiles",
    "shimpi": "Textiles",
    "शिंपी": "Textiles",
    "teacher": "Salaried",
    "salaried": "Salaried",
    "naukri": "Salaried",
    "worker": "Wage labour",
    "mazdoor": "Wage labour",
    "मजूर": "Wage labour",
    "business": "Business",
    "vyapar": "Business",
    "व्यवसाय": "Business",
}

INCOME_BANDS = (
    (0, 10_000, "below_10k", 4),
    (10_000, 25_000, "10k_25k", 10),
    (25_000, 50_000, "25k_50k", 14),
    (50_000, 100_000, "50k_100k", 18),
    (100_000, None, "above_100k", 20),
)


def _reason(code: str, label: str, points: int, detail: str) -> dict[str, Any]:
    return {"code": code, "label": label, "points": points, "detail": detail}


def _income_band(amount: int) -> tuple[str, int]:
    for low, high, name, points in INCOME_BANDS:
        if amount >= low and (high is None or amount < high):
            return name, points
    return "unknown", 0


def _occupation_category(value: str) -> str | None:
    lowered = value.lower()
    for keyword, category in OCCUPATION_STABILITY.items():
        if keyword in lowered:
            return category
    return None


def build_assessment(
    extracted: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    documents = documents or []
    reasons: list[dict[str, Any]] = []
    score = 0

    filled = [field for field in REQUIRED_FIELDS if (extracted.get(field) or {}).get("value")]
    missing = [field for field in REQUIRED_FIELDS if field not in filled]

    completion_points = round(len(filled) / len(REQUIRED_FIELDS) * 35)
    score += completion_points
    reasons.append(
        _reason(
            "FIELDS_COMPLETE" if not missing else "FIELDS_INCOMPLETE",
            "Required fields collected",
            completion_points,
            f"{len(filled)}/{len(REQUIRED_FIELDS)} required fields present"
            + (f"; missing: {', '.join(missing)}" if missing else ""),
        )
    )

    income_value = (extracted.get("monthly_income") or {}).get("value")
    if income_value:
        amount = parse_indian_amount(str(income_value))
        if amount:
            band, points = _income_band(amount)
            score += points
            reasons.append(
                _reason(
                    "INCOME_BAND",
                    "Stated income band",
                    points,
                    f"Stated monthly income is in the {band} band (applicant statement, not verified)",
                )
            )

    occupation = (extracted.get("occupation") or {}).get("value")
    if occupation:
        category = _occupation_category(str(occupation))
        points = 12 if category else 6
        score += points
        reasons.append(
            _reason(
                "OCCUPATION_STATED" if category else "OCCUPATION_UNCLEAR",
                "Occupation stated",
                points,
                f"Occupation maps to {category}" if category else "Occupation was stated but is not a recognised category",
            )
        )

    if (extracted.get("loan_purpose") or {}).get("value"):
        purpose = str(extracted["loan_purpose"]["value"])
        points = 8 if len(purpose) >= 10 else 4
        score += points
        reasons.append(
            _reason("PURPOSE_STATED", "Loan purpose stated", points, "A stated purpose is available for agent review")
        )

    if (extracted.get("phone") or {}).get("value"):
        score += 5
        reasons.append(
            _reason("PHONE_PRESENT", "Contact number present", 5, "A syntactically valid mobile number is on file")
        )

    if not documents:
        reasons.append(
            _reason(
                "NO_DOCUMENT",
                "No identity document uploaded",
                0,
                "No PAN/Aadhaar document was supplied; agent must collect one",
            )
        )
    else:
        overall = {doc.get("cross_check", {}).get("overall") for doc in documents}
        if "inconsistent" in overall:
            score -= 10
            reasons.append(
                _reason(
                    "DOCUMENT_MISMATCH",
                    "Document cross-check mismatch",
                    -10,
                    "At least one uploaded document contradicts the spoken name or date of birth",
                )
            )
        elif "consistent" in overall:
            score += 15
            reasons.append(
                _reason(
                    "DOCUMENT_CONSISTENT",
                    "Document consistent with spoken fields",
                    15,
                    "Uploaded document agrees with the spoken name/date of birth; still not identity verification",
                )
            )
        else:
            reasons.append(
                _reason(
                    "DOCUMENT_NOT_CHECKED",
                    "Document could not be cross-checked",
                    0,
                    "The document did not contain a comparable name or date of birth",
                )
            )

    score = max(0, min(100, score))
    if score < 40:
        band = "limited_evidence"
        recommendation = "priority_manual_review"
    elif score < 70:
        band = "moderate_evidence"
        recommendation = "standard_manual_review"
    else:
        band = "stronger_evidence"
        recommendation = "standard_manual_review"

    return {
        "score": score,
        "max_score": 100,
        "evidence_band": band,
        "recommendation": recommendation,
        "reason_codes": [reason["code"] for reason in reasons],
        "reasons": reasons,
        "documents_considered": len(documents),
        "disclaimer": (
            "Rule-based evidence summary for human review only. It is not a credit score, "
            "does not verify identity, and never approves or rejects an application."
        ),
    }
