from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from voice_grammar_agent.config import Settings
from voice_grammar_agent.llm import resolve_model

SYSTEM_PROMPT = """You fix grammar in spoken English.

Rules:
- Fix grammar, tense, articles, prepositions, word order, and repeated words.
- Keep the same meaning. Do not add facts or questions the user did not say.
- Keep person names EXACTLY as written (same spelling).
- Expand only obvious speech shortcuts like "I M" -> "I am", "hay" -> "hi".
- Return ONE corrected sentence only.
- No quotes, labels, or explanation."""

USER_TEMPLATE = """Spoken English:
{text}

Corrected English:"""


class GrammarCorrector(ABC):
    @abstractmethod
    def correct(self, text: str) -> str:
        raise NotImplementedError


class OllamaCorrector(GrammarCorrector):
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = model or settings.ollama_model

    def correct(self, text: str) -> str:
        spoken = text.strip()
        if not spoken:
            return spoken

        response = requests.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_TEMPLATE.format(text=spoken)},
                ],
                "stream": False,
                "options": {"temperature": 0, "num_predict": 120},
            },
            timeout=120,
        )
        response.raise_for_status()
        corrected = response.json().get("message", {}).get("content", "").strip()
        if not corrected:
            raise RuntimeError("Ollama returned an empty correction.")
        return _clean_output(corrected, spoken)


class LanguageToolCorrector(GrammarCorrector):
    def __init__(self) -> None:
        import language_tool_python

        self._tool = language_tool_python.LanguageTool("en-US")

    def correct(self, text: str) -> str:
        corrected = self._tool.correct(text)
        return corrected.strip() or text.strip()


def _clean_output(text: str, original: str) -> str:
    cleaned = text.strip().strip('"').strip("'").strip()
    for prefix in (
        "Corrected English:",
        "Corrected sentence:",
        "Corrected:",
        "Rewrite:",
        "Output:",
    ):
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :].strip()
    if cleaned.lower() == original.lower():
        return original
    return cleaned or original


def build_corrector(settings: Settings) -> GrammarCorrector:
    model_name = resolve_model(settings.ollama_base_url, settings.ollama_model)
    if model_name:
        print(f"Using Ollama ({model_name}) for grammar correction.")
        return OllamaCorrector(settings, model=model_name)

    print(
        "Ollama is not available — using LanguageTool (limited grammar fixes).\n"
        f"Start Ollama and run: ollama pull {settings.ollama_model}"
    )
    return LanguageToolCorrector()
