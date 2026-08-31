from __future__ import annotations

from voice_grammar_agent.config import Settings, get_settings
from voice_grammar_agent.corrector import GrammarCorrector, build_corrector
from voice_grammar_agent.stt import SpeechToText
from voice_grammar_agent.tts import TextToSpeech
from voice_grammar_agent.tutor import EnglishTutor


class VoiceGrammarAgent:
    def __init__(
        self,
        settings: Settings | None = None,
        stt: SpeechToText | None = None,
        corrector: GrammarCorrector | None = None,
        tts: TextToSpeech | None = None,
        tutor: EnglishTutor | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._stt = stt or SpeechToText(self._settings)
        self._corrector = corrector or build_corrector(self._settings)
        self._tts = tts or TextToSpeech(self._settings)
        self._tutor = tutor or EnglishTutor(self._settings, tts=self._tts, stt=self._stt)

    def transcribe(self, audio: tuple | str | None) -> str:
        return self._stt.transcribe_audio(audio)

    def correct(self, text: str) -> str:
        return self._corrector.correct(text)

    def synthesize(self, text: str) -> str:
        return self._tts.synthesize(text)

    def process(self, audio: tuple | str | None, text: str) -> tuple[str, str, str | None, str]:
        if audio is not None:
            original = self.transcribe(audio)
        elif text and text.strip():
            original = text.strip()
        else:
            raise ValueError("Record your voice or type a sentence to begin.")

        corrected = self.correct(original)
        audio_path = self.synthesize(corrected)
        return original, corrected, audio_path, ""

    def run_once(self) -> None:
        original = self._stt.listen()
        print(f"You said: {original}")
        result = self._tutor.turn(
            session_id="cli",
            text=original,
            mode="free",
            avatar_id="maya",
            spoken=True,
        )
        print(f"Tutor: {result['reply']}")
        if result["grammar"].get("has_errors"):
            print(f"Corrected: {result['grammar'].get('corrected')}")
        print("Speaking...")
        self._tts.speak(result["reply"])

    def run_loop(self) -> None:
        print("AI English Tutor")
        print("Speak naturally. Press Ctrl+C to quit.\n")
        while True:
            try:
                self.run_once()
                print()
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except ValueError as exc:
                print(f"Notice: {exc}\n")
            except Exception as exc:
                print(f"Error: {exc}\n")
