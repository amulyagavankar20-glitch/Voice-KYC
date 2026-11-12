from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    app_name: str = "VoiceKYC API"
    app_env: str = "development"
    api_v1_prefix: str = "/api/v1"
    secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./voicekyc.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"

    gnani_api_key: str = ""
    # Public Gnani Speech API defaults. Override only if Gnani changes them.
    gnani_api_base_url: str = "https://api.vachana.ai"
    gnani_prisma_transcribe_path: str = "/stt/v3"
    gnani_timbre_synthesize_path: str = "/api/v1/tts/inference"
    gnani_stt_model: str = "prisma-v3.0"
    gnani_tts_model: str = "timbre-v2.5"
    # Leave empty to use the per-language defaults in services/providers.py.
    gnani_tts_voice: str = ""
    gnani_request_timeout_seconds: int = Field(default=45, ge=5, le=180)
    provider_retry_attempts: int = Field(default=3, ge=1, le=6)
    provider_retry_base_delay_seconds: float = Field(default=0.5, ge=0, le=5)

    # Local media storage. Relative paths resolve against the current working
    # directory, which is `backend/` for both uvicorn and pytest.
    storage_dir: str = "./data/uploads"
    max_audio_upload_mb: int = Field(default=15, ge=1, le=50)
    max_document_upload_mb: int = Field(default=10, ge=1, le=50)

    # App-level streaming STT. Audio is buffered into short windows and sent to
    # the batch Prisma endpoint, because the vendor streaming protocol is not
    # part of the verified contract. Prisma accepts clips up to 30 seconds.
    stream_window_seconds: float = Field(default=6.0, ge=2, le=25)
    stream_max_buffer_seconds: float = Field(default=25.0, ge=5, le=29)

    # Optional LLM extraction layer (Groq + GPT-OSS). Default `none` keeps the
    # deterministic rules-only behaviour. Never auto-approve; LLM only fills
    # fields, humans still decide.
    llm_provider: str = "none"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_request_timeout_seconds: int = Field(default=20, ge=5, le=120)
    seed_demo_application: bool = False

    # OCR is optional. When Tesseract is not installed the API accepts
    # applicant-supplied document text and labels it honestly.
    tesseract_cmd: str = "tesseract"

    # Support both `backend/.env` (documented setup) and the project-root `.env`.
    # Values from the process environment still take precedence over dotenv files.
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def prisma_configured(self) -> bool:
        return bool(self.gnani_api_key and self.gnani_api_base_url and self.gnani_prisma_transcribe_path)

    @property
    def timbre_configured(self) -> bool:
        return bool(self.gnani_api_key and self.gnani_api_base_url and self.gnani_timbre_synthesize_path)

    @property
    def llm_configured(self) -> bool:
        return (
            self.llm_provider.strip().lower() == "groq"
            and bool(self.groq_api_key)
            and bool(self.groq_base_url)
            and bool(self.groq_model)
        )

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).expanduser().resolve()

    @property
    def max_audio_bytes(self) -> int:
        return self.max_audio_upload_mb * 1024 * 1024

    @property
    def max_document_bytes(self) -> int:
        return self.max_document_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
