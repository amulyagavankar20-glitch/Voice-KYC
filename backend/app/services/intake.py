import re
import unicodedata
from datetime import date
from typing import Any

REQUIRED_FIELDS = ["full_name", "father_name", "date_of_birth", "address", "occupation", "monthly_income", "loan_purpose", "phone"]
FIELD_LABELS = {
    "full_name": "Full name", "father_name": "Father's full name", "date_of_birth": "Date of birth",
    "address": "Residential address", "occupation": "Occupation", "monthly_income": "Monthly income",
    "loan_purpose": "Loan purpose", "phone": "Mobile number"
}
QUESTIONS = {
    "hinglish": {
        "full_name": "Namaste! Aapka poora naam kya hai? Kripya first aur last name batayein.",
        "father_name": "Aapke pitaji ka poora naam kya hai?",
        "date_of_birth": "Aapki date of birth kya hai? DD/MM/YYYY format mein bata sakte hain.",
        "address": "Aapka poora current address kya hai? Gaon, taluka aur district batayein.",
        "occupation": "Aapka occupation kya hai? Aap kis tarah ka kaam karte hain?",
        "monthly_income": "Aapki approximate monthly income kitni hai? Rupees mein batayein.",
        "loan_purpose": "Aapko loan kis purpose ke liye chahiye?",
        "phone": "Aapka 10-digit mobile number kya hai?"
    },
    "marathi": {
        "full_name": "नमस्कार! कृपया तुमचे पूर्ण नाव सांगा.",
        "father_name": "तुमच्या वडिलांचे पूर्ण नाव काय आहे?",
        "date_of_birth": "तुमची जन्मतारीख काय आहे? DD/MM/YYYY या स्वरूपात सांगा.",
        "address": "तुमचा सध्याचा पूर्ण पत्ता सांगा. गाव, तालुका आणि जिल्हा सांगा.",
        "occupation": "तुम्ही कोणते काम करता किंवा तुमचा व्यवसाय काय आहे?",
        "monthly_income": "तुमचे अंदाजे मासिक उत्पन्न किती आहे? रुपये मध्ये सांगा.",
        "loan_purpose": "तुम्हाला कर्ज कोणत्या कारणासाठी हवे आहे?",
        "phone": "तुमचा १० अंकी मोबाईल क्रमांक सांगा."
    }
}
CLARIFY = {
    "hinglish": "Mujhe is field ke liye thodi aur jaankari chahiye. Kripya dobara, thoda detail mein batayein.",
    "marathi": "या माहितीसाठी आणखी तपशील आवश्यक आहे. कृपया पुन्हा थोडे स्पष्ट सांगाल का?"
}
# Shown only after a first unusable answer, so a retry carries new information
# (a concrete example) instead of repeating the identical question.
ANSWER_HINTS = {
    "hinglish": {
        "full_name": "Jaise: Ramesh Kumar (poora first aur last name).",
        "father_name": "Jaise: Suresh Kumar.",
        "date_of_birth": "Jaise: 12/03/1990.",
        "address": "Jaise: House 12, MG Road, Pune, Maharashtra.",
        "occupation": "Jaise: farmer, dukandar, driver, tailor.",
        "monthly_income": "Jaise: 15000 ya 1.5 lakh (mahine ka).",
        "loan_purpose": "Jaise: beej aur khad kharidne ke liye.",
        "phone": "Jaise: 9876543210.",
    },
    "marathi": {
        "full_name": "उदाहरण: रमेश कुमार (संपूर्ण नाव).",
        "father_name": "उदाहरण: सुरेश कुमार.",
        "date_of_birth": "उदाहरण: १२/०३/१९९०.",
        "address": "उदाहरण: घर क्र. १२, एमजी रोड, पुणे, महाराष्ट्र.",
        "occupation": "उदाहरण: शेतकरी, दुकानदार, चालक, शिंपी.",
        "monthly_income": "उदाहरण: १५००० किंवा १.५ लाख (महिन्याचे).",
        "loan_purpose": "उदाहरण: बियाणे आणि खत खरेदीसाठी.",
        "phone": "उदाहरण: ९८७६५४३२१०.",
    },
}
COMPLETION = {
    "hinglish": "Dhanyavaad. Aapki di hui jaankari record ho gayi hai. Ab ek authorized agent ise verify karega. Yeh abhi KYC verification ya loan approval nahi hai.",
    "marathi": "धन्यवाद. तुम्ही दिलेली माहिती नोंदवली आहे. आता अधिकृत प्रतिनिधी तिची पडताळणी करेल. ही अंतिम KYC पडताळणी किंवा कर्ज मंजुरी नाही."
}

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

# Indian English / Hinglish multipliers used when people speak amounts aloud or
# type them ("1.5 lakh", "50k", "2 crore").
INCOME_MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000, "thousands": 1_000, "hazar": 1_000, "hazaar": 1_000, "हजार": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "लाख": 100_000,
    "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "karor": 10_000_000,
    "करोड़": 10_000_000, "करोड": 10_000_000,
}


def next_missing(extracted: dict[str, Any]) -> str | None:
    return next((field for field in REQUIRED_FIELDS if not extracted.get(field, {}).get("value")), None)


def progress(extracted: dict[str, Any]) -> int:
    filled = sum(bool(extracted.get(field, {}).get("value")) for field in REQUIRED_FIELDS)
    return round(filled / len(REQUIRED_FIELDS) * 100)


def normalize_indic_digits(value: str) -> str:
    devanagari = "०१२३४५६७८९"
    return value.translate(str.maketrans(devanagari, "0123456789"))


def _build_date(day: int, month: int, year: int) -> date | None:
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    today = date.today()
    if parsed > today or parsed.year < 1900:
        return None
    return parsed


def parse_date_of_birth(value: str) -> date | None:
    """Accept the numeric formats people actually use, plus month names."""
    numeric = re.search(r"\b(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})\b", value)
    if numeric:
        day, month, year = (int(part) for part in numeric.groups())
        return _build_date(day, month, year)
    iso = re.search(r"\b(\d{4})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})\b", value)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
        return _build_date(day, month, year)
    named = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", value)
    if named:
        day, month_name, year = named.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            return _build_date(int(day), month, int(year))
    return None


def parse_indian_amount(value: str) -> int | None:
    """Parse numeric amounts with lakh/crore/k multipliers.

    Returns None when no number is present. Currency symbols and commas are
    ignored, so "Rs. 15,000", "1.5 lakh" and "50k" all work.
    """
    cleaned = value.replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z\u0900-\u097F]*)", cleaned)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    multiplier = 1
    # Longest key first so "karor" is not mistaken for the "k" suffix.
    for suffix in sorted(INCOME_MULTIPLIERS, key=len, reverse=True):
        if unit == suffix or (unit.startswith(suffix) and unit[len(suffix):] in ("", "s")):
            multiplier = INCOME_MULTIPLIERS[suffix]
            break
    return int(round(number * multiplier))


def validate_field(field: str, raw: str) -> tuple[str | None, str | None]:
    """Conservative syntax checks only. These do not verify identity or truthfulness."""
    value = normalize_indic_digits(raw.strip())
    if not value:
        return None, "Please provide a response."
    if field in ("full_name", "father_name"):
        # Script-agnostic: only letters, combining marks (Devanagari vowel
        # signs/virama) and the punctuation real names use. Punctuation-only
        # input such as "!!!" is not a name.
        core = value.replace(" ", "").replace(".", "").replace("-", "").replace("'", "")
        is_name_char = all(
            ch.isalpha() or unicodedata.category(ch).startswith("M") for ch in core
        )
        if not core or not is_name_char or sum(ch.isalpha() for ch in core) < 2:
            return None, "Please provide the complete name using letters."
        if len(value) > 80:
            return None, "Please provide just the name, without extra sentences."
    elif field == "date_of_birth":
        parsed = parse_date_of_birth(value)
        if parsed is None:
            return None, "Please provide a valid date of birth in DD/MM/YYYY format."
        value = parsed.strftime("%d/%m/%Y")
    elif field == "address" and len(value) < 10:
        return None, "Please provide a more complete address, including village or locality and district."
    elif field == "occupation" and len(value) < 2:
        return None, "Please describe your occupation."
    elif field == "monthly_income":
        amount = parse_indian_amount(value)
        if amount is None:
            return None, "Please provide an approximate monthly amount in rupees."
        if amount <= 0 or amount > 100_000_000:
            return None, "Please provide a valid monthly income amount."
        value = str(amount)
    elif field == "loan_purpose" and len(value) < 4:
        return None, "Please describe what you need the loan for."
    elif field == "phone":
        digits = re.sub(r"\D", "", value)
        if digits.startswith("0091"):
            digits = digits[4:]
        elif len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != 10 or digits[0] not in "6789":
            return None, "Please provide a valid 10-digit Indian mobile number."
        value = digits
    return value, None


def make_field_record(
    value: str,
    evidence: str | None = None,
    extraction_source: str = "rules",
    raw_span: str | None = None,
) -> dict[str, Any]:
    # No fabricated model confidence: syntactic acceptance is not an AI confidence score.
    record = {
        "value": value,
        "confidence": None,
        "confidence_type": "not_assessed",
        "source": "applicant_statement",
        "needs_review": True,
        "extraction_source": extraction_source,
    }
    if evidence:
        # The applicant's own words that support this value, for the reviewer.
        record["evidence"] = evidence
    if raw_span:
        record["raw_span"] = raw_span
    return record


def build_clarification_reply(language: str, field: str, error: str | None, attempt: int) -> str:
    """Re-ask the current field, escalating to a concrete example after a retry.

    The application never advances on an unusable answer, so a repeated reply
    must give the applicant something new to work with instead of looping the
    identical question.
    """
    parts = [error or CLARIFY[language]]
    if attempt >= 1:
        hint = ANSWER_HINTS.get(language, ANSWER_HINTS["hinglish"]).get(field)
        if hint:
            parts.append(hint)
    parts.append(QUESTIONS[language][field])
    return "\n\n".join(parts)


FOLLOW_UP_PREFIX = {
    "hinglish": "Agent ko aapki application review karte waqt thodi aur jaankari chahiye.",
    "marathi": "प्रतिनिधीला तुमच्या अर्जाची पडताळणी करताना थोडी अधिक माहिती आवश्यक आहे.",
}
MISMATCH_PREFIX = {
    "hinglish": "Aapke document aur aapke bataye gaye {field} mein antar dikh raha hai. Kripya ise dobara confirm karein.",
    "marathi": "तुमच्या कागदपत्रात आणि तुम्ही सांगितलेल्या {field} मध्ये फरक दिसत आहे. कृपया पुन्हा खात्री करून सांगा.",
}


def build_follow_up(language: str, field: str, mismatch: list | None = None) -> str:
    """Compose an agent-initiated follow-up in the applicant's language.

    This is a deterministic template, not generated prose, so it cannot invent a
    reason or a claim. A document mismatch adds a confirmation request.
    """
    parts = [FOLLOW_UP_PREFIX[language]]
    if mismatch:
        label = FIELD_LABELS.get(field, field)
        parts.append(MISMATCH_PREFIX[language].format(field=label))
    parts.append(QUESTIONS[language][field])
    return " ".join(parts)
