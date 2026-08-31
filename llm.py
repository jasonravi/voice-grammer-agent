from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import requests

from voice_grammar_agent.config import Settings

FAST_MODELS = (
    "llama3.2:3b",
    "llama3.2:1b",
    "phi3:mini",
    "phi3",
    "gemma2:2b",
    "qwen2.5:3b",
    "llama3.2",
    "mistral",
    "llama3",
)


def list_chat_models(base_url: str) -> list[str]:
    try:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=10)
        if response.status_code != 200:
            return []
        models = []
        for entry in response.json().get("models", []):
            name = entry.get("name", "")
            capabilities = entry.get("capabilities") or []
            details = entry.get("details") or {}
            family = details.get("family", "")
            if "embedding" in capabilities or "bert" in family:
                continue
            if name:
                models.append(name)
        return models
    except requests.RequestException:
        return []


def _match(name: str, wanted: str) -> bool:
    return name == wanted or name.startswith(f"{wanted}:") or name.split(":")[0] == wanted.split(":")[0]


def resolve_model(base_url: str, preferred: str) -> str | None:
    models = list_chat_models(base_url)
    if not models:
        return None
    for name in models:
        if _match(name, preferred):
            return name
    for wanted in FAST_MODELS:
        for name in models:
            if _match(name, wanted):
                return name
    return models[0]


class LLMClient(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, num_predict: int = 90) -> str:
        raise NotImplementedError


class OllamaLLM(LLMClient):
    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._model = model or settings.ollama_model

    @property
    def model(self) -> str:
        return self._model

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, num_predict: int = 90) -> str:
        response = requests.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "messages": messages,
                "stream": False,
                "keep_alive": "30m",
                "options": {
                    "temperature": temperature,
                    "num_predict": num_predict,
                    "num_ctx": 2048,
                    "top_k": 30,
                    "top_p": 0.9,
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "").strip()
        if not content:
            raise RuntimeError("Ollama returned an empty reply.")
        return content

    def warmup(self) -> None:
        try:
            self.chat([{"role": "user", "content": "Hi"}], temperature=0, num_predict=4)
        except Exception:
            pass


GENERIC_REPLIES = {
    "tell me more",
    "tell me more.",
    "let's try that again a little more slowly",
    "let's try that again a little more slowly.",
    "ok",
    "okay",
    "yes",
    "hmm",
}


def collapse_repeats(text: str) -> str:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ""
    chunks = [part.strip(" ,.") for part in re.split(r"[,.!?]+", raw) if part.strip(" ,.")]
    if len(chunks) >= 3:
        from collections import Counter

        counts = Counter(part.lower() for part in chunks)
        phrase, n = counts.most_common(1)[0]
        if n >= 3 and n >= len(chunks) * 0.5:
            kept = next(part for part in chunks if part.lower() == phrase)
            leftover = [part for part in chunks if part.lower() != phrase]
            return kept if not leftover else f"{kept}. {leftover[0]}"
    words = raw.split()
    for size in range(6, 1, -1):
        if len(words) < size * 3:
            continue
        unit = words[:size]
        repeats = 0
        index = 0
        while index + size <= len(words) and words[index : index + size] == unit:
            repeats += 1
            index += size
        if repeats >= 3:
            return " ".join(unit)
    return raw


_TOPIC_STOP = {
    "i", "me", "my", "we", "you", "your", "a", "an", "the", "to", "of", "in", "on", "at",
    "and", "or", "but", "for", "with", "was", "were", "is", "are", "am", "be", "been",
    "did", "do", "does", "go", "went", "going", "have", "had", "has", "that", "this",
    "it", "so", "just", "very", "please", "ask", "any", "question", "today", "yesterday",
    "weekend", "something", "about", "like", "want", "can", "could", "would", "there",
}


def topic_from_spoken(spoken: str) -> str:
    words = re.findall(r"[A-Za-z']+", spoken or "")
    keep = [word for word in words if word.lower() not in _TOPIC_STOP and len(word) > 2]
    return keep[-1] if keep else ""


def follow_from_spoken(spoken: str) -> str:
    topic = topic_from_spoken(collapse_repeats(spoken))
    if topic:
        return f"Nice. Tell me more about {topic}."
    return "I am listening. Tell me more."


def fallback_say(spoken: str) -> str:
    return follow_from_spoken(spoken)


def is_generic_reply(text: str) -> bool:
    return (text or "").strip().lower().strip(".!") in {item.strip(".!") for item in GENERIC_REPLIES} or len((text or "").strip()) < 10


def _extract_say(cleaned: str) -> str:
    match = re.search(r"^SAY:\s*(.+)$", cleaned, re.I | re.M)
    if match:
        return match.group(1).strip().strip('"')
    match = re.search(r"SAY:\s*(.+)", cleaned, re.I)
    if match:
        line = re.split(r"\b(?:FIX|FEEL|HI|PRON|WORD|TIP):", match.group(1), maxsplit=1)[0]
        return line.strip().strip('"')
    for line in cleaned.splitlines():
        line = line.strip().strip('"')
        if not line or line.startswith("{") or line.startswith("```"):
            continue
        if re.match(r"^(FIX|FEEL|HI|PRON|WORD|TIP)\s*:", line, re.I):
            continue
        return line
    return ""


class EchoLLM(LLMClient):
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, num_predict: int = 90) -> str:
        spoken = ""
        for item in reversed(messages):
            if item.get("role") == "user":
                spoken = (item.get("content") or "").split("\n")[0].strip()
                break
        return (
            f"SAY: {fallback_say(spoken)}\nFIX: SAME\nFEEL: calm\n"
            "HI: अब अंग्रेज़ी में पूरा वाक्य बोलो।\nTIP: NONE"
        )


def build_llm(settings: Settings) -> LLMClient:
    model = resolve_model(settings.ollama_base_url, settings.ollama_model)
    if model:
        print(f"Using Ollama ({model}) for the English tutor.")
        client = OllamaLLM(settings, model=model)
        return client
    print(
        "Ollama is not available — using a limited offline fallback.\n"
        f"Start Ollama and run: ollama pull {settings.ollama_model}"
    )
    return EchoLLM()


def _related_rewrite(spoken: str, candidate: str) -> bool:
    stop = {"i", "you", "a", "an", "the", "to", "and", "or", "my", "me", "it", "is", "can", "say", "we"}
    spoken_words = set(re.findall(r"[a-z0-9]+", (spoken or "").lower())) - stop
    candidate_words = set(re.findall(r"[a-z0-9]+", (candidate or "").lower())) - stop
    if not spoken_words or not candidate_words:
        return False
    return len(spoken_words & candidate_words) / len(spoken_words) >= 0.45


def _correction_from_say(reply: str, spoken: str) -> str:
    match = re.search(
        r"(?:you can say|a clearer way|try saying)\s*:\s*(.+?)(?:[.?!](?:\s|$)|$)",
        reply or "",
        re.I,
    )
    if not match:
        return ""
    candidate = match.group(1).strip().strip('"')
    if not candidate or candidate.lower() == spoken.strip().lower():
        return ""
    if not _related_rewrite(spoken, candidate):
        return ""
    return candidate


def _grammar_payload(spoken: str, corrected: str) -> dict[str, Any]:
    cleaned = (corrected or spoken).strip() or spoken
    has_errors = cleaned.lower() != spoken.strip().lower()
    return {
        "has_errors": has_errors,
        "errors": (
            [
                {
                    "original": spoken,
                    "issue": "grammar",
                    "why": "A more accurate sentence is clearer for listeners.",
                    "corrected": cleaned,
                    "natural": cleaned,
                }
            ]
            if has_errors
            else []
        ),
        "corrected": cleaned,
        "natural": cleaned,
    }


def parse_tutor_reply(text: str, spoken: str) -> dict[str, Any]:
    data = {
        "reply": "",
        "expression": "encouraging",
        "grammar": _grammar_payload(spoken, spoken),
        "scores": {},
        "vocabulary_new": [],
        "exercise": "",
        "hindi": "",
        "pronunciation_tip": "",
        "word": "",
        "emotion": "calm",
    }
    cleaned = text.strip()
    try:
        parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.M).strip())
        if isinstance(parsed, dict) and parsed.get("reply"):
            grammar = parsed.get("grammar") if isinstance(parsed.get("grammar"), dict) else {}
            corrected = str(grammar.get("corrected") or spoken)
            extracted = _correction_from_say(str(parsed.get("reply") or ""), spoken)
            if extracted and corrected.strip().lower() == spoken.strip().lower():
                parsed["grammar"] = _grammar_payload(spoken, extracted)
            parsed.setdefault("hindi", "")
            parsed.setdefault("pronunciation_tip", parsed.get("pron") or "")
            parsed.setdefault("word", "")
            parsed.setdefault("exercise", parsed.get("tip") or "")
            parsed.setdefault("emotion", parsed.get("feel") or "calm")
            if is_generic_reply(str(parsed.get("reply") or "")):
                parsed["reply"] = fallback_say(spoken)
            return parsed
    except json.JSONDecodeError:
        pass

    fix = re.search(r"^FIX:\s*(.+)$", cleaned, re.I | re.M)
    tip = re.search(r"^TIP:\s*(.+)$", cleaned, re.I | re.M)
    hindi = re.search(r"^HI:\s*(.+)$", cleaned, re.I | re.M)
    pron = re.search(r"^PRON:\s*(.+)$", cleaned, re.I | re.M)
    word = re.search(r"^WORD:\s*(.+)$", cleaned, re.I | re.M)
    feel = re.search(r"^FEEL:\s*(.+)$", cleaned, re.I | re.M)
    data["reply"] = _extract_say(cleaned)
    corrected = (fix.group(1).strip() if fix else spoken).strip()
    if corrected.upper() == "SAME":
        corrected = spoken
    extracted = _correction_from_say(data["reply"], spoken)
    if extracted and corrected.lower() == spoken.lower():
        corrected = extracted
    if is_generic_reply(data["reply"]):
        data["reply"] = fallback_say(spoken)
    data["reply"] = re.sub(
        r"^(SAY|FIX|HI|PRON|WORD|TIP):\s*",
        "",
        data["reply"],
        flags=re.I,
    ).strip().strip('"')
    data["grammar"] = _grammar_payload(spoken, corrected)
    exercise = (tip.group(1).strip() if tip else "")
    data["exercise"] = "" if exercise.upper() == "NONE" else exercise
    hindi_text = (hindi.group(1).strip() if hindi else "")
    data["hindi"] = "" if hindi_text.upper() == "NONE" else hindi_text
    pron_text = (pron.group(1).strip() if pron else "")
    data["pronunciation_tip"] = "" if pron_text.upper() == "NONE" else pron_text
    word_text = (word.group(1).strip() if word else "")
    data["word"] = "" if word_text.upper() == "NONE" else word_text
    feel_text = (feel.group(1).strip() if feel else "calm").split()[0].lower()
    data["emotion"] = feel_text or "calm"
    if data["word"]:
        data["vocabulary_new"] = [data["word"]]
    return data


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    return {"reply": cleaned, "expression": "thinking", "grammar": {"has_errors": False, "errors": []}}
