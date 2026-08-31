from __future__ import annotations

import re
import threading
import uuid
from typing import Any

from voice_grammar_agent.avatars import get_avatar, list_avatars
from voice_grammar_agent.config import Settings, get_settings
from voice_grammar_agent.grammar_agent import GrammarReport, report_from_dict
from voice_grammar_agent.llm import (
    LLMClient,
    build_llm,
    collapse_repeats,
    fallback_say,
    follow_from_spoken,
    is_generic_reply,
    parse_tutor_reply,
    topic_from_spoken,
)
from voice_grammar_agent.modes import get_mode, list_modes
from voice_grammar_agent.progress import ProgressStore
from voice_grammar_agent.stt import SpeechToText
from voice_grammar_agent.tts import TextToSpeech

TUTOR_SYSTEM = """You are {name}, a warm English teacher in a live conversation.
{mode_prompt}
Lesson focus: {focus}

This is a real talk, not a quiz. Listen to the student's last message. React to a detail they said. Then ask one NEW follow-up about THAT detail.

Output format:
SAY: English only. One short human reaction plus one new question about their words. If their English is wrong, first give the correct sentence.
FIX: full corrected English sentence, or SAME
FEEL: calm
HI: one short Hindi line for the correction, or Hindi of your new question
TIP: NONE

Rules:
- Never use a question script. Never recycle "What did you do today?" or "What did you do over the weekend?"
- Never repeat a question you already asked.
- Never mention grammar rules in SAY. React to the meaning, like a friend, then ask about their words.
- English in SAY. Hindi only in HI.
- Keep SAY under 24 words.
"""

EMOTION_CUES = (
    ("bored", ("bored", "boring", "tired", "sleepy", "dull", "nothing to do", "thak", "bore")),
    ("sad", ("sad", "upset", "lonely", "cry", "udaas", "dukhi")),
    ("frustrated", ("angry", "annoyed", "frustrated", "hate", "gussa", "irritat")),
    ("confused", ("confused", "don't understand", "dont understand", "kya matlab", "samajh nahi", "samajh nahin")),
    ("nervous", ("nervous", "scared", "shy", "worried", "interview", "dar")),
    ("excited", ("excited", "amazing", "wow", "can't wait", "cant wait")),
    ("happy", ("happy", "great", "good", "love", "khush", "accha", "theek")),
)

HANGING_TAILS = {
    "will", "would", "can", "could", "should", "must", "may", "might",
    "am", "is", "are", "was", "were", "have", "has", "had", "do", "does", "did",
    "to", "and", "but", "or", "the", "a", "an", "my", "your", "i",
}

FEEL_TO_FACE = {
    "happy": "happy",
    "excited": "happy",
    "surprised": "surprised",
    "bored": "thinking",
    "confused": "thinking",
    "sad": "encouraging",
    "frustrated": "surprised",
    "nervous": "encouraging",
    "calm": "encouraging",
}

FEEL_OPENERS = {
    "bored": "I hear you feel bored.",
    "sad": "I hear that you feel low.",
    "frustrated": "I can hear the frustration.",
    "confused": "No worry, we will make it clear.",
    "nervous": "It is okay to feel nervous.",
    "excited": "I love that energy.",
    "happy": "That sounds good.",
}


def detect_emotion(text: str) -> str:
    lowered = (text or "").lower()
    for label, cues in EMOTION_CUES:
        if any(cue in lowered for cue in cues):
            return label
    if "?" in (text or "") and len((text or "").split()) <= 2:
        return "confused"
    return "calm"


def hanging_sentence(text: str) -> bool:
    words = re.findall(r"[A-Za-z']+", text or "")
    if len(words) < 4:
        return False
    if (text or "").strip().endswith("?"):
        return False
    last = words[-1].lower()
    return last in HANGING_TAILS


def _normalize(text: str) -> str:
    lowered = (text or "").lower()
    lowered = lowered.replace("i'd", "i would").replace("i'm", "i am").replace("don't", "do not")
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def style_only_fix(spoken: str, corrected: str) -> bool:
    return _normalize(spoken) == _normalize(corrected)


def extra_detail_only(spoken: str, corrected: str) -> bool:
    a, b = _normalize(spoken), _normalize(corrected)
    if not a or not b or a == b:
        return False
    return a in b and 0 < len(b.split()) - len(a.split()) <= 4


def _clip_reply(text: str, max_words: int = 36) -> str:
    parts = [part.strip() for part in re.split(r"(?<=[.?!])\s+", (text or "").strip()) if part.strip()]
    if not parts:
        return ""
    meta = re.compile(r"\b(past tense|simple past|grammar|correctly|you used|subject-verb|article)\b", re.I)
    useful = [part for part in parts if not meta.search(part)] or parts[:1]
    questions = [part for part in useful if part.endswith("?")]
    reactions = [part for part in useful if not part.endswith("?")]
    reaction = reactions[0] if reactions else ""
    question = questions[-1] if questions else ""
    kept = [part for part in (reaction, question) if part]
    clipped = " ".join(kept)
    words = clipped.split()
    if len(words) > max_words:
        clipped = " ".join(words[:max_words])
    return clipped


def looks_like_request(spoken: str) -> bool:
    low = (spoken or "").lower()
    return any(
        phrase in low
        for phrase in (
            "ask me",
            "please ask",
            "any question",
            "ask a question",
            "tell me a question",
        )
    )


def quick_english_fix(spoken: str) -> str:
    if looks_like_request(spoken):
        return ""
    original = (spoken or "").strip()
    text = original
    text = re.sub(r"\bgo(?:es)? to park\b", "went to the park", text, flags=re.I)
    text = re.sub(r"\bgo(?:es)? park\b", "went to the park", text, flags=re.I)
    if re.search(r"\b(yesterday|last night|last week|last weekend)\b", text, re.I):
        text = re.sub(r"\bI go\b", "I went", text)
        text = re.sub(r"\bI see\b", "I saw", text)
        text = re.sub(r"\bI eat\b", "I ate", text)
        text = re.sub(r"\bI come\b", "I came", text)
        text = re.sub(r"\bI do\b", "I did", text)
        text = re.sub(r"\bI buy\b", "I bought", text)
        text = re.sub(r"\bI play\b", "I played", text)
    text = re.sub(r"\bto park\b", "to the park", text, flags=re.I)
    if not text or _normalize(text) == _normalize(original):
        return ""
    return text[0].upper() + text[1:]


def echoes_student(reply: str, spoken: str) -> bool:
    first = re.split(r"[.?!]", reply or "", maxsplit=1)[0].strip()
    first = re.sub(r"^(say it like this|you can say|try this)\s*:?\s*", "", first, flags=re.I)
    spoken_n = _normalize(spoken)
    first_n = _normalize(first)
    if not spoken_n or not first_n:
        return False
    return spoken_n == first_n or spoken_n in first_n


SCRIPT_QUESTIONS = {
    "what did you do today",
    "what did you do over the weekend",
    "what else did you do",
    "how are you today",
    "what do you want to talk about today",
    "can you say that again",
    "what is your favorite hobby",
    "what topic do you want to talk about today",
}


def asked_questions(history: list[dict[str, Any]]) -> set[str]:
    asked: set[str] = set()
    for item in history or []:
        if item.get("role") != "assistant":
            continue
        for question in re.findall(r"[^.!?]*\?", item.get("content") or ""):
            asked.add(_normalize(question))
    return asked


def last_question(text: str) -> str:
    found = re.findall(r"[^.!?]*\?", text or "")
    return found[-1].strip() if found else ""


def swap_question(reply: str, question: str) -> str:
    text = (reply or "").strip()
    if re.search(r"[^.!?]*\?\s*$", text):
        return re.sub(r"[^.!?]*\?\s*$", question, text).strip()
    return f"{text} {question}".strip()


def is_scripted_question(question: str, asked: set[str]) -> bool:
    normalized = _normalize(question)
    if not normalized:
        return True
    return normalized in asked or normalized in SCRIPT_QUESTIONS


def with_fresh_question(reply: str, spoken: str, asked: set[str]) -> str:
    question = last_question(reply)
    if question and not is_scripted_question(question, asked):
        return reply
    follow = follow_from_spoken(spoken)
    if is_scripted_question(follow, asked):
        topic = topic_from_spoken(spoken)
        follow = f"How did {topic} go?" if topic else "What happened after that?"
        if is_scripted_question(follow, asked):
            follow = "What is one small detail I should know?"
    return swap_question(reply, follow)


def fallback_hindi(spoken: str, grammar: GrammarReport) -> str:
    corrected = (grammar.corrected or "").strip()
    if grammar.has_errors and corrected and not style_only_fix(spoken, corrected):
        return f"सही वाक्य: {corrected}। इसे अंग्रेज़ी में दोहराओ।"
    if _looks_hindi(spoken):
        return "अंग्रेज़ी में पूरा वाक्य बोलो।"
    return "अच्छा। अब अंग्रेज़ी में और बताओ।"


def hindi_is_usable(text: str) -> bool:
    value = (text or "").strip()
    if not value or value.upper() == "NONE":
        return False
    if re.search(r"[\u0900-\u097F]", value):
        return True
    return bool(
        re.search(
            r"\b(sahi|vakya|angrezi|bolo|dobara|aap|kya|kaise|accha|theek)\b",
            value,
            re.I,
        )
    )


def _spoken_reply(reply: str, spoken_text: str, grammar: GrammarReport, asked: set[str]) -> str:
    reply = _clip_reply((reply or "").strip())
    if is_generic_reply(reply):
        reply = follow_from_spoken(spoken_text)
    corrected = (grammar.corrected or "").strip()
    real_fix = (
        grammar.has_errors
        and corrected
        and not style_only_fix(spoken_text, corrected)
    )
    if real_fix and not re.match(r"^(say it like this|you can say)\b", reply, re.I):
        question = last_question(reply)
        follow = question if question and not is_scripted_question(question, asked) else follow_from_spoken(spoken_text)
        reply = _clip_reply(f"Say it like this: {corrected} {follow}")
    return with_fresh_question(reply, spoken_text, asked)


def complete_hanging(text: str) -> str:
    spoken = re.sub(r"\s+", " ", (text or "").strip()).rstrip(".!,")
    if not spoken:
        return spoken
    if spoken.lower().endswith(" i will"):
        return f"{spoken} try something new."
    if spoken.lower().endswith((" i am", " i'm")):
        return f"{spoken} ready to practice."
    return f"{spoken} something."


def _looks_hindi(text: str) -> bool:
    if re.search(r"[\u0900-\u097F]", text or ""):
        return True
    return bool(
        re.search(
            r"\b(haan|nahi|nahin|kya|kaise|kyun|samajh|matlab|namaste|accha|theek hai)\b",
            text or "",
            re.I,
        )
    )


def estimate_speech_scores(text: str, spoken: bool) -> dict[str, int]:
    words = re.findall(r"[A-Za-z']+", text.lower())
    fillers = sum(1 for word in words if word in {"um", "uh", "erm", "like", "yeah"})
    repeats = sum(1 for a, b in zip(words, words[1:]) if a == b)
    length = len(words)
    fluency = 88
    pronunciation = 84 if spoken else 70
    if length < 4:
        fluency -= 8
    if length >= 12:
        fluency += 4
    fluency -= fillers * 6
    fluency -= repeats * 8
    pronunciation -= fillers * 5
    if not spoken:
        pronunciation = max(pronunciation - 8, 50)
    return {
        "fluency": int(max(40, min(98, fluency))),
        "pronunciation": int(max(40, min(98, pronunciation))),
    }


def clamp_scores(raw: Any, spoken_text: str, spoken: bool, grammar: GrammarReport) -> dict[str, int]:
    data = raw if isinstance(raw, dict) else {}
    speech = estimate_speech_scores(spoken_text, spoken)
    grammar_score = data.get("grammar")
    if grammar_score is None:
        grammar_score = 62 if grammar.has_errors else 92
        grammar_score -= min(20, 6 * len(grammar.errors))
    vocab_score = data.get("vocabulary")
    if vocab_score is None:
        unique = len(set(re.findall(r"[A-Za-z']+", spoken_text.lower())))
        vocab_score = min(95, 60 + unique * 3)
    return {
        "grammar": int(max(0, min(100, grammar_score))),
        "vocabulary": int(max(0, min(100, vocab_score))),
        "pronunciation": int(max(0, min(100, data.get("pronunciation", speech["pronunciation"])))),
        "fluency": int(max(0, min(100, data.get("fluency", speech["fluency"])))),
    }


class EnglishTutor:
    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        tts: TextToSpeech | None = None,
        stt: SpeechToText | None = None,
        progress: ProgressStore | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._llm = llm or build_llm(self._settings)
        self._tts = tts or TextToSpeech(self._settings)
        self._stt = stt or SpeechToText(self._settings)
        self._progress = progress or ProgressStore(self._settings.db_path)
        if hasattr(self._llm, "warmup"):
            threading.Thread(target=self._llm.warmup, daemon=True).start()
        threading.Thread(target=self._stt.warmup, daemon=True).start()

    def catalog(self) -> dict:
        model = getattr(self._llm, "model", None)
        return {
            "model": model or "offline-fallback",
            "ollama": model is not None,
            "avatars": list_avatars(),
            "modes": list_modes(),
            "fast_voice": True,
        }

    def transcribe(self, audio) -> str:
        return self._stt.transcribe_audio(audio)

    def start_session(self, mode: str, avatar_id: str, user_id: str = "local") -> dict:
        session_id = uuid.uuid4().hex
        avatar = get_avatar(avatar_id)
        learning = get_mode(mode)
        self._progress.create_session(session_id, user_id, learning.id, avatar.id)
        lesson = self._progress.lesson_focus(user_id)
        opening = (
            f"Hi, I am {avatar.name}. Let's talk in English. "
            "Tell me anything you want — I am listening."
        )
        hindi = f"नमस्ते, मैं {avatar.name} हूँ। जो मन हो अंग्रेज़ी में बताओ।"
        if learning.roleplay:
            opening = (
                f"Hi, I am {avatar.name}. Let's role-play a {learning.roleplay}. "
                "You start. What do you say first?"
            )
            hindi = f"चलो {learning.roleplay} का रोल-प्ले करते हैं। पहले आप बोलो।"
        elif lesson:
            opening = (
                f"Hi, I am {avatar.name}. We can practice {lesson['label']} while we talk. "
                "Tell me anything you want — I am listening."
            )
            hindi = f"आज {lesson['label']} भी सीखेंगे। जो मन हो अंग्रेज़ी में बताओ।"
        self._progress.add_turn(session_id, "assistant", opening, expression="happy")
        return {
            "session_id": session_id,
            "avatar": avatar.id,
            "mode": learning.id,
            "reply": opening,
            "expression": "happy",
            "lesson": lesson,
            "hindi": hindi,
            "pronunciation_tip": "",
            "audio_path": None,
            "visemes": [],
            "words": [],
            "duration_ms": 0,
            "fast_voice": True,
        }

    def turn(
        self,
        *,
        session_id: str,
        text: str,
        mode: str,
        avatar_id: str,
        user_id: str = "local",
        spoken: bool = True,
        audio=None,
    ) -> dict:
        if audio is not None and not (text or "").strip():
            text = self.transcribe(audio)
        spoken_text = collapse_repeats((text or "").strip())
        if not spoken_text:
            raise ValueError("I did not catch that. Please say it again.")

        avatar = get_avatar(avatar_id)
        learning = get_mode(mode)
        lesson = self._progress.lesson_focus(user_id)
        focus = lesson["label"] if lesson else "confident spoken English"
        self._progress.create_session(session_id, user_id, learning.id, avatar.id)
        self._progress.add_turn(session_id, "user", spoken_text)

        history = self._progress.history(session_id, self._settings.history_turns)
        asked = asked_questions(history)
        system = TUTOR_SYSTEM.format(
            name=avatar.name,
            mode_prompt=learning.prompt,
            focus=focus,
        )
        felt = detect_emotion(spoken_text)
        banned = "; ".join(q for q in sorted(asked) if q) or "none"
        notes = [
            "React to these exact words like a person. Ask one NEW follow-up about a detail they mentioned.",
            f"Do not repeat these questions: {banned}.",
        ]
        if hanging_sentence(spoken_text):
            notes.append("The sentence is unfinished. FIX a complete English sentence.")
        if felt != "calm":
            notes.append(f"They sound {felt}. Stay kind.")
        if _looks_hindi(spoken_text):
            notes.append("They used Hindi. FIX the English sentence they should say. Explain in HI.")
        user_payload = f'Student said: "{spoken_text}"\n' + " ".join(notes)
        messages = [{"role": "system", "content": system}]
        for item in history[-10:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            if role == "assistant" and is_generic_reply(content):
                continue
            messages.append({"role": role, "content": content[:280]})
        if messages and messages[-1]["role"] == "user":
            messages[-1] = {"role": "user", "content": user_payload}
        else:
            messages.append({"role": "user", "content": user_payload})

        try:
            raw = self._llm.chat(messages, temperature=0.65, num_predict=110)
        except Exception:
            raw = (
                f"SAY: {follow_from_spoken(spoken_text)}\nFIX: SAME\nFEEL: calm\n"
                "HI: अब अंग्रेज़ी में और बताओ।\nTIP: NONE"
            )
        payload = parse_tutor_reply(raw, spoken_text)
        grammar = report_from_dict(payload, spoken_text)
        if looks_like_request(spoken_text):
            payload["grammar"] = {
                "has_errors": False,
                "corrected": spoken_text,
                "natural": spoken_text,
                "errors": [],
            }
            grammar = report_from_dict(payload, spoken_text)
        elif style_only_fix(spoken_text, grammar.corrected) or extra_detail_only(
            spoken_text, grammar.corrected
        ):
            payload["grammar"] = {
                "has_errors": False,
                "corrected": spoken_text,
                "natural": spoken_text,
                "errors": [],
            }
            grammar = report_from_dict(payload, spoken_text)
        quick = quick_english_fix(spoken_text)
        if quick and not grammar.has_errors:
            payload["grammar"] = {
                "has_errors": True,
                "corrected": quick,
                "natural": quick,
                "errors": [
                    {
                        "original": spoken_text,
                        "issue": "grammar",
                        "why": "Use a complete past-tense sentence.",
                        "corrected": quick,
                        "natural": quick,
                    }
                ],
            }
            grammar = report_from_dict(payload, spoken_text)
        if hanging_sentence(spoken_text) and not grammar.has_errors:
            finished = complete_hanging(spoken_text)
            payload["grammar"] = {
                "has_errors": True,
                "corrected": finished,
                "natural": finished,
                "errors": [
                    {
                        "original": spoken_text,
                        "issue": "incomplete sentence",
                        "why": "Finish the idea so the listener knows what you will do.",
                        "corrected": finished,
                        "natural": finished,
                    }
                ],
            }
            grammar = report_from_dict(payload, spoken_text)
        emotion = str(payload.get("emotion") or felt or "calm").lower()
        if emotion not in FEEL_TO_FACE:
            emotion = felt
        scores = clamp_scores(payload.get("scores"), spoken_text, spoken, grammar)
        reply = _spoken_reply(
            str(payload.get("reply") or ""),
            spoken_text,
            grammar,
            asked,
        )
        if not grammar.has_errors and echoes_student(reply, spoken_text):
            reply = with_fresh_question(f"I like that. {follow_from_spoken(spoken_text)}", spoken_text, asked)
        if is_generic_reply(reply):
            reply = with_fresh_question(follow_from_spoken(spoken_text), spoken_text, asked)
        hindi = str(payload.get("hindi") or "")
        if grammar.has_errors:
            if not hindi_is_usable(hindi):
                hindi = fallback_hindi(spoken_text, grammar)
        elif not hindi_is_usable(hindi):
            hindi = "अच्छा। अब अंग्रेज़ी में और बताओ।"
        exercise = str(payload.get("exercise") or "")
        if exercise and exercise.upper() != "NONE" and not is_scripted_question(exercise, asked):
            if last_question(reply) and is_scripted_question(last_question(reply), asked):
                reply = swap_question(reply, exercise if exercise.endswith("?") else f"{exercise}?")
        pronunciation_tip = str(payload.get("pronunciation_tip") or "")
        expression = str(payload.get("expression") or FEEL_TO_FACE.get(emotion, "encouraging"))
        if expression not in {"happy", "encouraging", "thinking", "surprised", "listening"}:
            expression = FEEL_TO_FACE.get(emotion, "encouraging")
        vocab = [str(word) for word in payload.get("vocabulary_new") or [] if str(word).strip()]
        if payload.get("word") and str(payload["word"]).strip() not in vocab:
            vocab.append(str(payload["word"]).strip())

        self._progress.add_turn(
            session_id,
            "assistant",
            reply,
            grammar=grammar.to_dict(),
            scores=scores,
            expression=expression,
        )
        self._progress.record_mistakes(user_id, grammar.to_dict()["errors"])
        self._progress.record_vocabulary(user_id, vocab, spoken_text)

        return {
            "session_id": session_id,
            "user_text": spoken_text,
            "reply": reply,
            "expression": expression,
            "grammar": grammar.to_dict(),
            "scores": scores,
            "vocabulary_new": vocab,
            "exercise": exercise,
            "hindi": hindi,
            "pronunciation_tip": pronunciation_tip,
            "emotion": emotion,
            "lesson": lesson,
            "audio_path": None,
            "visemes": [],
            "words": [],
            "duration_ms": 0,
            "fast_voice": True,
        }

    def end_session(self, session_id: str) -> dict:
        report = self._progress.session_report(session_id)
        return self._progress.end_session(session_id, report["scores"], report)

    def progress(self, user_id: str = "local") -> dict:
        return self._progress.progress(user_id)
