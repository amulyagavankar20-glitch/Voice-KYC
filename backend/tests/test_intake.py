from app.services.intake import parse_date_of_birth, parse_indian_amount, validate_field, progress, next_missing


def test_valid_phone():
    assert validate_field("phone", "+91 98765 43210")[0] == "9876543210"


def test_phone_accepts_leading_zero_and_country_prefix():
    assert validate_field("phone", "098765 43210")[0] == "9876543210"
    assert validate_field("phone", "+91-9876543210")[0] == "9876543210"


def test_invalid_phone_rejected():
    assert validate_field("phone", "12345")[0] is None


def test_invalid_date_rejected():
    assert validate_field("date_of_birth", "31/02/1998")[0] is None


def test_future_date_rejected():
    assert validate_field("date_of_birth", "01/01/2999")[0] is None


def test_date_formats_normalized():
    assert validate_field("date_of_birth", "12/03/1990")[0] == "12/03/1990"
    assert validate_field("date_of_birth", "1990-03-12")[0] == "12/03/1990"
    assert validate_field("date_of_birth", "12 March 1990")[0] == "12/03/1990"
    assert validate_field("date_of_birth", "१२/०३/१९९०")[0] == "12/03/1990"


def test_income_normalized():
    assert validate_field("monthly_income", "Rs. 15,000")[0] == "15000"


def test_income_indian_units():
    assert validate_field("monthly_income", "1.5 lakh")[0] == "150000"
    assert validate_field("monthly_income", "50k")[0] == "50000"
    assert validate_field("monthly_income", "2 crore")[0] == "20000000"
    assert parse_indian_amount("Rs. 1,50,000") == 150000
    assert parse_indian_amount("no amount here") is None


def test_invalid_income_rejected():
    assert validate_field("monthly_income", "0")[0] is None


def test_parsed_dob_is_a_date():
    assert parse_date_of_birth("12/03/1990").isoformat() == "1990-03-12"


def test_progress_and_missing():
    extracted = {"full_name": {"value": "Test Person"}}
    assert progress(extracted) == 12
    assert next_missing(extracted) == "father_name"


def test_name_must_contain_letters():
    assert validate_field("full_name", "!!!")[0] is None
    assert validate_field("full_name", "12345")[0] is None
    assert validate_field("full_name", ".")[0] is None


def test_name_accepts_devanagari_and_hyphenated_names():
    assert validate_field("full_name", "रमेश कुमार")[0] == "रमेश कुमार"
    assert validate_field("full_name", "Ramesh Kumar-Singh")[0] == "Ramesh Kumar-Singh"
    assert validate_field("full_name", "A. P. J. Abdul Kalam")[0] == "A. P. J. Abdul Kalam"


def test_overlong_name_rejected():
    assert validate_field("full_name", "Ramesh " * 20)[0] is None
