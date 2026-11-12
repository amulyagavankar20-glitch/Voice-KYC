from app.services.scorecard import build_assessment
from app.services.intake import REQUIRED_FIELDS

FULL = {
    "full_name": {"value": "Ramesh Kumar"},
    "father_name": {"value": "Suresh Kumar"},
    "date_of_birth": {"value": "12/03/1990"},
    "address": {"value": "House 12, MG Road, Pune, Maharashtra"},
    "occupation": {"value": "Farmer"},
    "monthly_income": {"value": "150000"},
    "loan_purpose": {"value": "Buy seeds and fertilizer"},
    "phone": {"value": "9876543210"},
}


def test_empty_application_gets_priority_review():
    result = build_assessment({})
    assert result["reason_codes"][0] == "FIELDS_INCOMPLETE"
    assert result["recommendation"] == "priority_manual_review"
    assert result["score"] >= 0
    assert "not a credit score" in result["disclaimer"]


def test_complete_application_has_completion_and_income_reasons():
    result = build_assessment(FULL)
    assert "FIELDS_COMPLETE" in result["reason_codes"]
    assert "INCOME_BAND" in result["reason_codes"]
    assert "OCCUPATION_STATED" in result["reason_codes"]
    assert result["score"] >= 60
    assert result["recommendation"] in {"standard_manual_review", "priority_manual_review"}
    assert all(reason["points"] >= -100 for reason in result["reasons"])


def test_document_consistency_adds_points_and_mismatch_subtracts():
    consistent = build_assessment(
        FULL,
        [{"doc_type": "pan", "cross_check": {"overall": "consistent"}}],
    )
    mismatch = build_assessment(
        FULL,
        [{"doc_type": "pan", "cross_check": {"overall": "inconsistent"}}],
    )
    assert "DOCUMENT_CONSISTENT" in consistent["reason_codes"]
    assert "DOCUMENT_MISMATCH" in mismatch["reason_codes"]
    assert consistent["score"] > mismatch["score"]
    assert mismatch["recommendation"] in {"standard_manual_review", "priority_manual_review"}


def test_missing_document_is_explicit():
    result = build_assessment(FULL)
    assert "NO_DOCUMENT" in result["reason_codes"]


def test_assessment_never_emits_an_approval():
    result = build_assessment(FULL, [{"doc_type": "pan", "cross_check": {"overall": "consistent"}}])
    assert result["recommendation"] in {"standard_manual_review", "priority_manual_review"}
    assert result["score"] <= result["max_score"]
    assert set(result["reason_codes"]) & {"APPROVE", "REJECT", "CREDIT_SCORE"} == set()


def test_every_required_field_missing_is_reported():
    result = build_assessment({"full_name": {"value": "Ramesh Kumar"}})
    assert "full_name" not in result["reasons"][0]["detail"]
    for field in REQUIRED_FIELDS[1:]:
        assert field in result["reasons"][0]["detail"]
