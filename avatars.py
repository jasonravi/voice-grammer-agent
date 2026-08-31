from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Avatar:
    id: str
    name: str
    title: str
    voice: str
    accent: str
    palette: str


AVATARS: dict[str, Avatar] = {
    "maya": Avatar(
        "maya",
        "Maya",
        "Warm conversation tutor",
        "en-US-JennyNeural",
        "American",
        "sage",
    ),
    "noah": Avatar(
        "noah",
        "Noah",
        "Clear pronunciation coach",
        "en-US-GuyNeural",
        "American",
        "navy",
    ),
    "priya": Avatar(
        "priya",
        "Priya",
        "Patient grammar tutor",
        "en-IN-NeerjaNeural",
        "Indian English",
        "amber",
    ),
}


def list_avatars() -> list[dict[str, str]]:
    return [
        {
            "id": avatar.id,
            "name": avatar.name,
            "title": avatar.title,
            "voice": avatar.voice,
            "accent": avatar.accent,
            "palette": avatar.palette,
        }
        for avatar in AVATARS.values()
    ]


def get_avatar(avatar_id: str) -> Avatar:
    return AVATARS.get(avatar_id) or AVATARS["maya"]
