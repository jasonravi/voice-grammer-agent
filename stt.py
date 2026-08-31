from __future__ import annotations

import json
import os
import threading
import wave

import numpy as np
import requests
import speech_recognition as sr

# The speech model is already cached locally. Hugging Face HEAD requests hang
# forever behind a 403 proxy, so never go online to load Whisper.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from voice_grammar_agent.config import Settings

SAMPLE_RATE = 16_000
BLOCK_SIZE = 1024
SILENCE_RMS_THRESHOLD = 350
GOOGLE_SPEECH_URL = "https://www.google.com/speech-api/v2/recognize"
GOOGLE_SPEECH_KEY = "AIzaSyBOti4mM-6x9WDnZIjIeyEU21OpBXqWBgw"
WHISPER_MODEL = "openai/whisper-tiny.en"


class SpeechToText:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._recognizer = sr.Recognizer()

    def _record_until_silence(self) -> bytes:
        import sounddevice as sd

        silence_blocks_needed = max(
            1, int(self._settings.pause_threshold * SAMPLE_RATE / BLOCK_SIZE)
        )
        max_blocks = int(self._settings.phrase_time_limit * SAMPLE_RATE / BLOCK_SIZE)

        recorded: list[np.ndarray] = []
        silent_count = 0
        started = False

        print("Listening... (speak now, pause when done)")

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCK_SIZE,
        ) as stream:
            for _ in range(max_blocks):
                block, _ = stream.read(BLOCK_SIZE)
                block = block.flatten()
                rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))

                if rms > SILENCE_RMS_THRESHOLD:
                    started = True
                    silent_count = 0
                    recorded.append(block)
                elif started:
                    silent_count += 1
                    recorded.append(block)
                    if silent_count >= silence_blocks_needed:
                        break

        if not recorded:
            raise ValueError("No speech detected. Please try again.")

        return np.concatenate(recorded).tobytes()

    def _recognize_google(self, audio: sr.AudioData) -> str:
        sample_rate = audio.sample_rate if audio.sample_rate >= 8000 else 8000
        pcm = audio.get_raw_data(convert_rate=sample_rate, convert_width=2)
        response = requests.post(
            GOOGLE_SPEECH_URL,
            params={
                "client": "chromium",
                "lang": self._settings.language,
                "key": GOOGLE_SPEECH_KEY,
                "pFilter": 0,
            },
            data=pcm,
            headers={"Content-Type": f"audio/l16; rate={sample_rate}"},
            timeout=20,
        )
        response.raise_for_status()
        return self._parse_google_transcript(response.text)

    def _recognize_whisper(self, samples: np.ndarray, sample_rate: int) -> str:
        audio = _resample_mono(samples, sample_rate, SAMPLE_RATE)
        audio = _trim_silence(audio)
        max_len = SAMPLE_RATE * 5
        if audio.size > max_len:
            audio = audio[:max_len]
        if audio.size < SAMPLE_RATE * 0.25 or float(np.max(np.abs(audio))) < 0.004:
            return ""
        try:
            result = _whisper_pipeline()(
                {"array": audio, "sampling_rate": SAMPLE_RATE},
                generate_kwargs={
                    "max_new_tokens": 48,
                    "temperature": 0.0,
                },
            )
        except Exception:
            result = _whisper_pipeline()(
                {"array": audio, "sampling_rate": SAMPLE_RATE},
                generate_kwargs={"max_new_tokens": 48},
            )
        text = str(result.get("text") or "").strip()
        if not text or set(text) <= set(".…,!? "):
            return ""
        from voice_grammar_agent.llm import collapse_repeats

        return collapse_repeats(text)

    def transcribe_audio(self, audio: tuple[int, np.ndarray] | str | None) -> str:
        if audio is None:
            raise ValueError("No audio provided. Record or upload audio first.")

        if isinstance(audio, str):
            samples, rate = _load_wav(audio)
            text = self._recognize_whisper(samples, rate)
            if not text:
                raise ValueError("I did not catch that. Tap the mic and say it again.")
            return text

        sample_rate, data = audio
        pcm = self._to_mono_int16(data)
        recorded = sr.AudioData(pcm.tobytes(), sample_rate, 2)
        return self._recognize(recorded)

    def _recognize(self, audio: sr.AudioData) -> str:
        samples = np.frombuffer(
            audio.get_raw_data(convert_width=2), dtype=np.int16
        ).astype(np.float32) / 32768.0
        sample_rate = audio.sample_rate if audio.sample_rate >= 8000 else 8000
        transcript = self._recognize_whisper(samples, sample_rate)
        if transcript:
            return transcript
        raise ValueError("I did not catch that. Tap the mic and say it again.")

    @staticmethod
    def _parse_google_transcript(response_text: str) -> str:
        for line in response_text.splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            results = payload.get("result") or []
            if not results:
                continue
            alternatives = results[0].get("alternative") or []
            if alternatives and alternatives[0].get("transcript"):
                return alternatives[0]["transcript"].strip()
        return ""

    @staticmethod
    def _to_mono_int16(data: np.ndarray) -> np.ndarray:
        if data.dtype in (np.float32, np.float64):
            data = (data * 32767).astype(np.int16)
        else:
            data = data.astype(np.int16)

        if data.ndim > 1:
            data = data.mean(axis=1).astype(np.int16)
        return data

    def listen(self) -> str:
        audio_bytes = self._record_until_silence()

        print("Processing speech...")
        audio = sr.AudioData(audio_bytes, SAMPLE_RATE, 2)
        return self._recognize(audio)

    def warmup(self) -> None:
        try:
            _whisper_pipeline()
        except Exception as exc:
            print(f"Speech model warmup skipped: {exc}")


def _load_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32), rate


def _trim_silence(audio: np.ndarray) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < 0.004:
        return audio
    thresh = max(0.004, peak * 0.08)
    voiced = np.flatnonzero(np.abs(audio) > thresh)
    if voiced.size == 0:
        return audio
    pad = int(SAMPLE_RATE * 0.12)
    start = max(0, int(voiced[0]) - pad)
    end = min(audio.size, int(voiced[-1]) + pad)
    trimmed = audio[start:end]
    return trimmed if trimmed.size else audio


def _resample_mono(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if src_rate == dst_rate or audio.size == 0:
        return audio
    duration = audio.size / float(src_rate)
    new_len = max(1, int(round(duration * dst_rate)))
    old_x = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    new_x = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
    return np.interp(new_x, old_x, audio).astype(np.float32)


_WHISPER_LOCK = threading.Lock()
_WHISPER = None


def _whisper_pipeline():
    global _WHISPER
    if _WHISPER is not None:
        return _WHISPER
    with _WHISPER_LOCK:
        if _WHISPER is not None:
            return _WHISPER
        from transformers import WhisperForConditionalGeneration, WhisperProcessor, pipeline

        processor = WhisperProcessor.from_pretrained(WHISPER_MODEL, local_files_only=True)
        model = WhisperForConditionalGeneration.from_pretrained(
            WHISPER_MODEL,
            local_files_only=True,
        )
        _WHISPER = pipeline(
            "automatic-speech-recognition",
            model=model,
            tokenizer=processor.tokenizer,
            feature_extractor=processor.feature_extractor,
            device=-1,
        )
        return _WHISPER
