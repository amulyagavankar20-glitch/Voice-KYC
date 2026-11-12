from app.services.documents import (
    aadhaar_valid,
    compare_dob,
    compare_names,
    cross_check,
    extract_fields,
    mask_number,
    pan_valid,
    verhoeff_check_digit,
    verhoeff_valid,
)


def test_pan_shape_is_enforced():
    assert pan_valid("ABCDE1234F")
    assert not pan_valid("ABCD1234F")
    assert not pan_valid("abcde1234f")


def test_aadhaar_verhoeff_checksum():
    base = "23456789012"
    number = base + verhoeff_check_digit(base)
    assert verhoeff_valid(number)
    assert aadhaar_valid(number)
    assert not aadhaar_valid(base + "0")


def test_aadhaar_must_not_start_with_zero_or_one():
    base = "03456789012"
    number = base + verhoeff_check_digit(base)
    assert verhoeff_valid(number)
    assert not aadhaar_valid(number)


def test_extract_pan_document_fields():
    fields = extract_fields("Name: Ramesh Kumar\nDOB: 12/03/1990\nPAN: ABCDE1234F", "pan")
    assert fields["document_number"] == "ABCDE1234F"
    assert fields["number_masked"] == "XXXXXX234F"
    assert fields["number_valid"] is True
    assert fields["full_name"] == "Ramesh Kumar"
    assert fields["date_of_birth"] == "12/03/1990"


def test_extract_aadhaar_document_fields():
    base = "23456789012"
    number = base + verhoeff_check_digit(base)
    fields = extract_fields(f"Name: Ramesh Kumar\nAadhaar: {number}", "aadhaar")
    assert fields["document_number"] == number
    assert fields["number_masked"] == "XXXX XXXX " + number[-4:]
    assert fields["number_valid"] is True


def test_extract_missing_number_is_empty():
    assert "document_number" not in extract_fields("Name: Ramesh Kumar", "pan")


def test_mask_number_keeps_last_four_only():
    assert mask_number("ABCDE1234F") == "XXXXXX234F"
    assert mask_number("12") == "XX"


def test_names_compare_on_tokens_not_exact_strings():
    assert compare_names("Ramesh Kumar", "RAMESH KUMAR")["status"] == "consistent"
    assert compare_names("Ramesh Kumar", "Ramesh K. Kumar")["status"] == "consistent"
    assert compare_names("Ramesh Kumar", "Suresh Kumar")["status"] == "inconsistent"
    assert compare_names("Ramesh Kumar", None)["status"] == "not_checked"


def test_dob_comparison_uses_parsed_dates():
    assert compare_dob("12/03/1990", "12 March 1990")["status"] == "consistent"
    assert compare_dob("12/03/1990", "12/03/1991")["status"] == "inconsistent"
    assert compare_dob(None, "12/03/1990")["status"] == "not_checked"


def test_cross_check_marks_mismatch_for_review():
    extracted = {
        "full_name": {"value": "Ramesh Kumar"},
        "date_of_birth": {"value": "12/03/1990"},
    }
    result = cross_check(extracted, {"full_name": "Suresh Kumar", "date_of_birth": "12/03/1990"})
    assert result["overall"] == "inconsistent"
    assert result["review_flag"] is True
    assert [item["field"] for item in result["mismatches"]] == ["full_name"]
    assert "not identity verification" in result["note"]


def test_cross_check_consistent_does_not_claim_verification():
    extracted = {
        "full_name": {"value": "Ramesh Kumar"},
        "date_of_birth": {"value": "12/03/1990"},
    }
    result = cross_check(extracted, {"full_name": "Ramesh Kumar", "date_of_birth": "12/03/1990"})
    assert result["overall"] == "consistent"
    assert result["review_flag"] is False
    assert "not identity verification" in result["note"]
