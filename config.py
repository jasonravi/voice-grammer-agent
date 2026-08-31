from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    ollama_model: str
    tts_voice: str
    language: str
    phrase_time_limit: int
    pause_threshold: float
    db_path: str
    history_turns: int


def get_settings() -> Settings:
    return Settings(
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        tts_voice=os.getenv("TTS_VOICE", "en-US-JennyNeural"),
        language=os.getenv("LANGUAGE", "en-US"),
        phrase_time_limit=int(os.getenv("PHRASE_TIME_LIMIT", "15")),
        pause_threshold=float(os.getenv("PAUSE_THRESHOLD", "0.8")),
        db_path=os.getenv("TUTOR_DB_PATH", str(DATA_DIR / "tutor.db")),
        history_turns=int(os.getenv("HISTORY_TURNS", "12")),
    )
