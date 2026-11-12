from app.services.extraction import clarification_attempt, local_candidates
from app.services.intake import QUESTIONS


class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


def test_clarification_attempt_counts_prompts_for_the_current_field():
    question = QUESTIONS["hinglish"]["full_name"]
    messages = [Message("assistant", question)]
    assert clarification_attempt(messages, "hinglish", "full_name") == 0
    messages.append(Message("user", "12345"))
    messages.append(Message("assistant", f"Mujhe aur jaankari chahiye.\n\n{question}"))
    assert clarification_attempt(messages, "hinglish", "full_name") == 1
    assert clarification_attempt(messages, "hinglish", "father_name") == 0


def test_clarification_attempt_unknown_field_is_zero():
    assert clarification_attempt([], "hinglish", "credit_score") == 0


def test_local_candidates_strip_hinglish_framing():
    assert local_candidates("full_name", "Mera naam Ramesh Kumar hai.")[0] == "Ramesh Kumar"
    assert local_candidates("full_name", "My name is Ramesh Kumar")[0] == "Ramesh Kumar"
    assert local_candidates("father_name", "Mere pitaji ka naam Suresh Kumar hai.")[0] == "Suresh Kumar"
    assert local_candidates("occupation", "Main kheti karta hoon.")[0] == "kheti"
    assert local_candidates("occupation", "I am a farmer")[0] == "farmer"
    assert (
        local_candidates("address", "Main House 12, MG Road, Pune mein rehta hoon.")[0]
        == "House 12, MG Road, Pune"
    )


def test_local_candidates_strip_marathi_framing():
    assert local_candidates("full_name", "माझे नाव रमेश कुमार आहे.")[0] == "रमेश कुमार"
    assert local_candidates("occupation", "मी शेती करतो.")[0] == "शेती"


def test_local_candidates_always_include_the_original_utterance():
    for field, text in [
        ("full_name", "Mera naam Ramesh Kumar hai."),
        ("occupation", "Main kheti karta hoon."),
        ("phone", "9876543210"),
        ("loan_purpose", "Mujhe beej kharidne ke liye loan chahiye."),
    ]:
        assert text in local_candidates(field, text)


def test_local_candidates_do_not_rewrite_fields_without_a_rule():
    assert local_candidates("phone", "9876543210") == ["9876543210"]
    assert local_candidates("monthly_income", "1.5 lakh") == ["1.5 lakh"]


def test_plain_name_is_not_mangled():
    assert local_candidates("full_name", "Ramesh Kumar")[0] == "Ramesh Kumar"


def test_local_candidates_do_not_invent_text():
    text = "Mera naam Ramesh Kumar hai."
    for candidate in local_candidates("full_name", text):
        for word in candidate.split():
            assert word in text
