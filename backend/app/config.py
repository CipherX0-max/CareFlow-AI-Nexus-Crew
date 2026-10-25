"""Environment-based configuration for CareFlow AI."""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    demo_mode: bool = os.getenv("DEMO_MODE", "true").lower() == "true"
    allow_paid_providers: bool = os.getenv("ALLOW_PAID_PROVIDERS", "false").lower() == "true"
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./careflow_demo.db")
    stt_provider: str = os.getenv("STT_PROVIDER", "demo_mock")
    stt_fallback_provider: str = os.getenv("STT_FALLBACK_PROVIDER", "demo_mock")
    ocr_provider: str = os.getenv("OCR_PROVIDER", "demo_mock")
    ocr_fallback_provider: str = os.getenv("OCR_FALLBACK_PROVIDER", "demo_mock")
    translation_provider: str = os.getenv("TRANSLATION_PROVIDER", "demo_mock")
    translation_fallback_provider: str = os.getenv("TRANSLATION_FALLBACK_PROVIDER", "demo_mock")
    structuring_provider: str = os.getenv("STRUCTURING_PROVIDER", "demo_mock")
    structuring_fallback_provider: str = os.getenv("STRUCTURING_FALLBACK_PROVIDER", "demo_mock")
    triage_engine: str = os.getenv("TRIAGE_ENGINE", "rules")
    triage_rules_version: str = os.getenv("TRIAGE_RULES_VERSION", "v2.4.0-demo-unvalidated")
    sarvam_api_key: str = os.getenv("SARVAM_API_KEY", "")
    sarvam_api_base_url: str = os.getenv("SARVAM_API_BASE_URL", "https://api.sarvam.ai")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    gemma_model_id: str = os.getenv("GEMMA_MODEL_ID", "gemma3:4b")


def get_settings() -> Settings:
    return Settings()
