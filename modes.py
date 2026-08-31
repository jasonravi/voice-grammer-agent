from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearningMode:
    id: str
    name: str
    blurb: str
    prompt: str
    roleplay: str | None = None


MODES: dict[str, LearningMode] = {
    "free": LearningMode(
        "free",
        "Free conversation",
        "Talk about anything. I keep the chat natural and coach gently.",
        "Have a real conversation. Follow the student's last words. Correct if needed, then ask a new follow-up about what they just said. Never use a fixed list of questions.",
    ),
    "grammar": LearningMode(
        "grammar",
        "Grammar practice",
        "I focus on tense, articles, prepositions, and sentence structure.",
        "Focus on grammar. After each student turn, name the rule, give a corrected sentence, and ask them to repeat it.",
    ),
    "interview": LearningMode(
        "interview",
        "Interview practice",
        "Job interview questions, STAR answers, and professional tone.",
        "You are a hiring manager. Ask one interview question at a time. Coach clearer, more confident answers.",
        "job interview",
    ),
    "daily": LearningMode(
        "daily",
        "Daily English",
        "Small talk, errands, family, and everyday situations.",
        "Practice everyday English: greetings, plans, opinions, and daily routines. Keep language practical.",
    ),
    "vocabulary": LearningMode(
        "vocabulary",
        "Vocabulary",
        "Learn useful words and use them in new sentences.",
        "Teach 1-2 useful words per turn. Give a short definition, an example, then ask the student to use the word.",
    ),
    "pronunciation": LearningMode(
        "pronunciation",
        "Pronunciation",
        "Rhythm, stress, and clearer sounds.",
        "Coach pronunciation and fluency. Suggest slower phrasing, stress, and a short phrase to repeat aloud.",
    ),
    "travel": LearningMode(
        "travel",
        "Travel",
        "Airports, hotels, directions, and sightseeing.",
        "Role-play travel English. Stay in character, then briefly coach better phrasing.",
        "travel",
    ),
    "restaurant": LearningMode(
        "restaurant",
        "Restaurant",
        "Ordering food, asking for the bill, polite requests.",
        "Role-play a restaurant conversation. Stay in character, then briefly coach natural phrases.",
        "restaurant",
    ),
    "meeting": LearningMode(
        "meeting",
        "Meetings",
        "Stand-ups, updates, and agreeing or disagreeing politely.",
        "Role-play a workplace meeting. Coach concise, professional English.",
        "workplace meeting",
    ),
    "workplace": LearningMode(
        "workplace",
        "Workplace",
        "Office chat, emails spoken aloud, and collaboration.",
        "Role-play workplace English with a colleague. Keep it practical and polite.",
        "workplace",
    ),
}


def list_modes() -> list[dict[str, str]]:
    return [{"id": mode.id, "name": mode.name, "blurb": mode.blurb} for mode in MODES.values()]


def get_mode(mode_id: str) -> LearningMode:
    return MODES.get(mode_id) or MODES["free"]
