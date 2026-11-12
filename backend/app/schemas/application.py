from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Language = Literal["hinglish", "marathi"]
Decision = Literal["approve", "request_info", "reject"]
DocType = Literal["pan", "aadhaar"]
FieldReviewStatus = Literal["verified", "flagged"]


class ChatStartRequest(BaseModel):
    language: Language = "hinglish"
    consent: bool = Field(..., description="Applicant must consent before information is collected")


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # At-most-once delivery for retries over an unreliable connection.
    client_message_id: str | None = Field(default=None, max_length=64)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank.")
        return value


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=3000)
    language: Language = "hinglish"

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Text must not be blank.")
        return value


class FieldValue(BaseModel):
    value: Any = None
    confidence: float | None = None
    confidence_type: str = "not_assessed"
    source: str = "applicant_statement"
    needs_review: bool = True
    evidence: str | None = None


class ChatResponse(BaseModel):
    application_id: str
    reply: str
    extracted_data: dict[str, Any]
    progress: int
    is_complete: bool
    status: str
    current_field: str | None = None
    ai_provider: str = "deterministic"
    duplicate: bool = False


class DecisionRequest(BaseModel):
    decision: Decision
    note: str | None = Field(default=None, max_length=1000)


class MessageOut(BaseModel):
    id: int | None = None
    role: str
    content: str
    channel: str
    audio_id: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ApplicationSummary(BaseModel):
    id: str
    name: str
    status: str
    language: str
    created_at: datetime
    updated_at: datetime
    progress: int
    confidence_avg: float | None = None
    review_priority: str | None = None
    evidence_score: int | None = None


class ApplicationDetail(BaseModel):
    id: str
    status: str
    language: str
    created_at: datetime
    updated_at: datetime
    extracted: dict[str, Any]
    reviewed_fields: dict[str, Any] = {}
    assessment: dict[str, Any] = {}
    conversation: list[MessageOut]
    decision: str | None
    decision_note: str | None


class FieldReviewRequest(BaseModel):
    status: FieldReviewStatus
    note: str | None = Field(default=None, max_length=500)


class FollowUpRequest(BaseModel):
    field: str | None = None
    # When true the question is also appended to the applicant transcript and
    # the application moves to `request_info`.
    send: bool = False


class DocumentOut(BaseModel):
    id: str
    application_id: str
    doc_type: str
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int = 0
    extracted_fields: dict[str, Any] = {}
    cross_check: dict[str, Any] = {}
    extraction_method: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AudioOut(BaseModel):
    id: str
    application_id: str
    message_id: int | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int = 0
    duration_ms: int | None = None
    status: str
    # Bytes acknowledged so far; lets a resumable client recover a lost response.
    received_bytes: int = 0
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
