from __future__ import annotations

import asyncio
import concurrent.futures
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts

from voice_grammar_agent.config import Settings

AUDIO_DIR = Path(tempfile.gettempdir()) / "english-tutor-audio"
AUDIO_DIR.mkdir(exist_ok=True)


@dataclass
class SpeechCues:
    path: str
    visemes: list[dict] = field(default_factory=list)
    words: list[dict] = field(default_factory=list)
    duration_ms: int = 0
    audio_url_hint: str = ""


VOWEL_SHAPE = {
    "a": 2,
    "e": 4,
    "i": 6,
    "o": 8,
    "u": 7,
    "y": 6,
}


def _ticks_to_ms(value: int | float | None) -> int:
    if not value:
        return 0
    return int(value / 10_000)


class TextToSpeech:
    def __init__(self, settings: Settings) -> None:
        self._voice = settings.tts_voice

    async def _synthesize(self, text: str, output_path: Path, voice: str) -> SpeechCues:
        communicate = edge_tts.Communicate(text, voice)
        audio = bytearray()
        visemes: list[dict] = []
        words: list[dict] = []
        async for chunk in communicate.stream():
            kind = chunk.get("type")
            if kind == "audio":
                audio.extend(chunk.get("data") or b"")
            elif kind == "WordBoundary":
                words.append(
                    {
                        "text": chunk.get("text") or "",
                        "offset_ms": _ticks_to_ms(chunk.get("offset")),
                        "duration_ms": _ticks_to_ms(chunk.get("duration")),
                    }
                )
            elif kind in {"Viseme", "viseme"}:
                visemes.append(
                    {
                        "id": int(chunk.get("id") or chunk.get("viseme_id") or 0),
                        "offset_ms": _ticks_to_ms(chunk.get("offset") or chunk.get("audio_offset")),
                    }
                )
        output_path.write_bytes(bytes(audio))
        if not visemes:
            visemes = visemes_from_words(words, text)
        duration = 0
        if words:
            last = words[-1]
            duration = last["offset_ms"] + last["duration_ms"]
        if visemes:
            duration = max(duration, visemes[-1]["offset_ms"] + 180)
        if duration <= 0:
            duration = max(800, int(len(text.split()) * 380))
        return SpeechCues(
            path=str(output_path),
            visemes=visemes,
            words=words,
            duration_ms=duration,
        )

    def synthesize_cues(self, text: str, voice: str | None = None) -> SpeechCues:
        spoken = (text or "").strip()
        if not spoken:
            raise ValueError("Nothing to speak.")
        output_path = AUDIO_DIR / f"{uuid.uuid4().hex}.mp3"
        chosen = voice or self._voice
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            cues = asyncio.run(self._synthesize(spoken, output_path, chosen))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                cues = pool.submit(asyncio.run, self._synthesize(spoken, output_path, chosen)).result()
        return cues

    def synthesize(self, text: str, voice: str | None = None) -> str:
        return self.synthesize_cues(text, voice=voice).path

    def speak(self, text: str) -> None:
        output_path = Path(self.synthesize(text))
        try:
            self._play(output_path)
        finally:
            output_path.unlink(missing_ok=True)

    def _play(self, path: Path) -> None:
        if sys.platform == "darwin":
            subprocess.run(["afplay", str(path)], check=True)
        elif sys.platform.startswith("linux"):
            subprocess.run(["mpg123", "-q", str(path)], check=True)
        elif sys.platform == "win32":
            subprocess.run(
                [
                    "powershell",
                    "-c",
                    f'(New-Object Media.SoundPlayer "{path}").PlaySync()',
                ],
                check=True,
            )
        else:
            raise RuntimeError(f"Unsupported platform for audio playback: {sys.platform}")


def visemes_from_words(words: list[dict], text: str) -> list[dict]:
    if not words:
        tokens = text.split()
        visemes = [{"id": 0, "offset_ms": 0}]
        cursor = 80
        for token in tokens:
            shape = VOWEL_SHAPE.get(next((ch for ch in token.lower() if ch in VOWEL_SHAPE), "a"), 2)
            visemes.append({"id": shape, "offset_ms": cursor})
            cursor += max(180, len(token) * 70)
            visemes.append({"id": 21, "offset_ms": cursor - 40})
        visemes.append({"id": 0, "offset_ms": cursor})
        return visemes

    visemes = [{"id": 0, "offset_ms": 0}]
    for word in words:
        start = word["offset_ms"]
        duration = max(word["duration_ms"], 120)
        letters = [ch for ch in word["text"].lower() if ch.isalpha()] or ["a"]
        step = duration / max(len(letters), 1)
        for index, letter in enumerate(letters):
            shape = VOWEL_SHAPE.get(letter, 19 if letter in "tdn" else 21 if letter in "pbm" else 15 if letter in "sz" else 4)
            visemes.append({"id": shape, "offset_ms": int(start + index * step)})
        visemes.append({"id": 0, "offset_ms": start + duration})
    return visemes
