"""Central settings. All values come from environment variables / .env (see .env.example)."""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
DEFAULT_SECRET = "change-me-development-only"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    app_env: str = "development"  # development | production
    secret_key: str = DEFAULT_SECRET
    database_url: str = f"sqlite:///{ROOT / 'data' / 'careflow.db'}"
    data_dir: Path = ROOT / "data"
    upload_dir: Path = ROOT / "data" / "uploads"

    # sessions / web security
    session_ttl_minutes: int = 480
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    rate_limit_auth_per_minute: int = 20
    rate_limit_enabled: bool = True
    # No email service is bundled. In development, verification/reset tokens are returned
    # in the API response so the flow can be exercised; this is refused in production.
    expose_dev_tokens: bool = True

    # local LLM (Ollama) -- backend only, never a cloud fallback
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b"          # Stage 1: structured fact extraction & translation
    medgemma_model: str = "medgemma1.5"     # Stage 2: medical evidence analysis, synthesis & adaptive follow-ups
    ollama_timeout_seconds: float = 180.0
    medgemma_timeout_seconds: float = 180.0
    ollama_keep_alive: str = "2m"
    llm_enabled: bool = True
    llm_conflict_opinions: bool = False  # advisory LLM interpretation of conflicts

    # local speech-to-text (faster-whisper)
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_beam_size: int = 3
    whisper_vad_filter: bool = True
    whisper_translate: bool = False  # False skips the extra Whisper translation pass by default; original transcript is authoritative
    whisper_model_dir: Path = ROOT / "data" / "models" / "whisper"
    whisper_local_only: bool = False  # True = never download; fail if the model is not cached
    stt_enabled: bool = True

    # OCR
    tesseract_cmd: str = ""
    tesseract_langs: str = "eng+hin"
    tessdata_dir: str = ""
    ocr_min_confidence: float = 60.0
    ocr_dpi: int = 250

    # intake validation
    min_patient_age: int = 1
    max_patient_age: int = 120
    default_country_code: str = "91"
    # synthetic-demo policy: allow patients to hard-delete their own submitted cases directly (never in production unless explicitly enabled)
    direct_deletion_enabled: bool = True

    # limits
    max_upload_mb: int = 20
    max_audio_mb: int = 25
    max_pdf_pages: int = 20
    max_audio_seconds: int = 300
    max_message_chars: int = 5000
    max_uploads_per_case: int = 15
    max_image_pixels: int = 60_000_000

    # rules / safety text
    rules_path: Path = PKG / "rules" / "demo_ruleset.json"
    knowledge_path: Path = PKG / "rules" / "demo_knowledge.json"
    emergency_number: str = ""  # locally configured; empty = generic wording

    @field_validator("data_dir", "upload_dir", "whisper_model_dir", "rules_path", "knowledge_path", mode="before")
    @classmethod
    def _path(cls, v):
        return Path(v)

    @field_validator("whisper_beam_size")
    @classmethod
    def _valid_beam_size(cls, v: int) -> int:
        if v < 1 or v > 10:
            raise ValueError("WHISPER_BEAM_SIZE must be between 1 and 10.")
        return v

    @property
    def cors_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    def validate_for_runtime(self) -> None:
        if self.is_production:
            if self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32:
                raise RuntimeError("SECRET_KEY must be a random value of 32+ characters in production.")
            if not self.cookie_secure:
                raise RuntimeError("COOKIE_SECURE must be true in production (serve over HTTPS).")
            if self.expose_dev_tokens:
                raise RuntimeError("EXPOSE_DEV_TOKENS must be false in production.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
