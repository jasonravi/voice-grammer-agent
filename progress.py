from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from voice_grammar_agent.config import get_settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProgressStore:
    def __init__(self, db_path: str | None = None) -> None:
        self._path = Path(db_path or get_settings().db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    avatar_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    grammar_score REAL,
                    vocabulary_score REAL,
                    pronunciation_score REAL,
                    fluency_score REAL,
                    summary_json TEXT
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    grammar_json TEXT,
                    scores_json TEXT,
                    expression TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mistakes (
                    user_id TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    example TEXT,
                    correction TEXT,
                    count INTEGER NOT NULL DEFAULT 1,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY (user_id, pattern)
                );
                CREATE TABLE IF NOT EXISTS vocabulary (
                    user_id TEXT NOT NULL,
                    word TEXT NOT NULL,
                    context TEXT,
                    first_seen TEXT NOT NULL,
                    PRIMARY KEY (user_id, word)
                );
                """
            )

    def create_session(self, session_id: str, user_id: str, mode: str, avatar_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, user_id, mode, avatar_id, started_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, user_id, mode, avatar_id, utcnow()),
            )

    def add_turn(
        self,
        session_id: str,
        role: str,
        text: str,
        grammar: dict | None = None,
        scores: dict | None = None,
        expression: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (session_id, role, text, grammar_json, scores_json, expression, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    text,
                    json.dumps(grammar) if grammar else None,
                    json.dumps(scores) if scores else None,
                    expression,
                    utcnow(),
                ),
            )

    def history(self, session_id: str, limit: int = 12) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, text FROM turns WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        messages = [{"role": row["role"], "content": row["text"]} for row in reversed(rows)]
        return messages

    def record_mistakes(self, user_id: str, errors: list[dict[str, Any]]) -> None:
        if not errors:
            return
        with self._connect() as conn:
            for error in errors:
                pattern = (error.get("issue") or "grammar").strip().lower()
                example = error.get("original") or ""
                correction = error.get("corrected") or error.get("natural") or ""
                existing = conn.execute(
                    "SELECT count FROM mistakes WHERE user_id = ? AND pattern = ?",
                    (user_id, pattern),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE mistakes
                        SET count = count + 1, example = ?, correction = ?, last_seen = ?
                        WHERE user_id = ? AND pattern = ?
                        """,
                        (example, correction, utcnow(), user_id, pattern),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO mistakes (user_id, pattern, example, correction, count, last_seen)
                        VALUES (?, ?, ?, ?, 1, ?)
                        """,
                        (user_id, pattern, example, correction, utcnow()),
                    )

    def record_vocabulary(self, user_id: str, words: list[str], context: str) -> None:
        with self._connect() as conn:
            for word in words:
                clean = " ".join(word.lower().split())
                if not clean:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO vocabulary (user_id, word, context, first_seen)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, clean, context, utcnow()),
                )

    def top_mistakes(self, user_id: str, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pattern, count, example, correction
                FROM mistakes WHERE user_id = ?
                ORDER BY count DESC, last_seen DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def lesson_focus(self, user_id: str) -> dict | None:
        mistakes = self.top_mistakes(user_id, limit=1)
        if not mistakes:
            return None
        top = mistakes[0]
        pattern = (top.get("pattern") or "grammar").lower()
        catalog = (
            ("tense", "past tense", "Tell me what you did yesterday in complete sentences."),
            ("subject", "subject-verb agreement", "Describe your family using he, she, and they."),
            ("article", "a, an, and the", "Describe the room you are in, using a, an, and the."),
            ("preposition", "prepositions", "Tell me where things are in your kitchen."),
            ("pronunc", "clearer pronunciation", "Repeat slowly: I want to speak English confidently."),
            ("vocab", "useful vocabulary", "Describe your work or studies in three sentences."),
            ("fluency", "fluency", "Talk about your morning without stopping to correct yourself."),
        )
        label, prompt = "sentence structure", "Make a full sentence about your day."
        for key, mapped_label, mapped_prompt in catalog:
            if key in pattern:
                label, prompt = mapped_label, mapped_prompt
                break
        return {
            "pattern": pattern,
            "label": label,
            "prompt": prompt,
            "example": top.get("example") or "",
            "correction": top.get("correction") or "",
            "count": top.get("count") or 1,
        }

    def weakness_summary(self, user_id: str) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT pattern, count, example, correction
                FROM mistakes WHERE user_id = ?
                ORDER BY count DESC, last_seen DESC LIMIT 5
                """,
                (user_id,),
            ).fetchall()
        if not rows:
            return "No recurring grammar issues yet."
        parts = [f"{row['pattern']} ({row['count']}x)" for row in rows]
        return "Recurring issues: " + "; ".join(parts)

    def end_session(self, session_id: str, scores: dict[str, float], summary: dict) -> dict:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET ended_at = ?, grammar_score = ?, vocabulary_score = ?,
                    pronunciation_score = ?, fluency_score = ?, summary_json = ?
                WHERE id = ?
                """,
                (
                    utcnow(),
                    scores.get("grammar"),
                    scores.get("vocabulary"),
                    scores.get("pronunciation"),
                    scores.get("fluency"),
                    json.dumps(summary),
                    session_id,
                ),
            )
        return self.session_report(session_id)

    def session_turns(self, session_id: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()

    def session_report(self, session_id: str) -> dict:
        turns = self.session_turns(session_id)
        user_turns = [row for row in turns if row["role"] == "user"]
        score_lists: dict[str, list[float]] = {
            "grammar": [],
            "vocabulary": [],
            "pronunciation": [],
            "fluency": [],
        }
        mistakes = []
        corrections = []
        vocab = []
        for row in user_turns:
            scores = json.loads(row["scores_json"] or "{}")
            for key in score_lists:
                if key in scores:
                    score_lists[key].append(float(scores[key]))
            grammar = json.loads(row["grammar_json"] or "{}")
            for error in grammar.get("errors") or []:
                mistakes.append(error)
                if error.get("corrected"):
                    corrections.append(
                        {"original": error.get("original") or row["text"], "corrected": error.get("corrected")}
                    )
            if grammar.get("corrected") and grammar.get("has_errors"):
                corrections.append({"original": row["text"], "corrected": grammar.get("corrected")})
        with self._connect() as conn:
            session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            user_id = session["user_id"] if session else "local"
            words = conn.execute(
                "SELECT word FROM vocabulary WHERE user_id = ? ORDER BY first_seen DESC LIMIT 12",
                (user_id,),
            ).fetchall()
            vocab = [row["word"] for row in words]
            top_mistakes = conn.execute(
                "SELECT pattern, count, example, correction FROM mistakes WHERE user_id = ? ORDER BY count DESC LIMIT 6",
                (user_id,),
            ).fetchall()

        averages = {
            key: round(sum(values) / len(values)) if values else 0 for key, values in score_lists.items()
        }
        unique_corrections = []
        seen = set()
        for item in corrections:
            stamp = (item["original"], item["corrected"])
            if stamp in seen:
                continue
            seen.add(stamp)
            unique_corrections.append(item)

        recommendations = []
        if averages["grammar"] < 80:
            recommendations.append("Spend a session in Grammar practice focusing on your top mistake pattern.")
        if averages["fluency"] < 80:
            recommendations.append("Try Daily English and speak in longer sentences without pausing to self-correct.")
        if averages["vocabulary"] < 80:
            recommendations.append("Use Vocabulary mode and reuse each new word in two original sentences.")
        if averages["pronunciation"] < 80:
            recommendations.append("Switch to Pronunciation mode and shadow Maya's sentence out loud.")
        if not recommendations:
            recommendations.append("Keep a 10-minute free conversation tomorrow and stretch into a new role-play.")

        return {
            "session_id": session_id,
            "mode": session["mode"] if session else "free",
            "turns": len(user_turns),
            "scores": averages,
            "common_mistakes": [dict(row) for row in top_mistakes],
            "corrected_sentences": unique_corrections[:8],
            "vocabulary": vocab,
            "recommendations": recommendations,
        }

    def progress(self, user_id: str = "local") -> dict:
        now = datetime.now(timezone.utc)
        windows = {
            "daily": now - timedelta(days=1),
            "weekly": now - timedelta(days=7),
            "monthly": now - timedelta(days=30),
        }
        with self._connect() as conn:
            sessions = conn.execute(
                """
                SELECT started_at, grammar_score, vocabulary_score, pronunciation_score, fluency_score, mode
                FROM sessions WHERE user_id = ? AND ended_at IS NOT NULL
                ORDER BY started_at
                """,
                (user_id,),
            ).fetchall()
            vocab_count = conn.execute(
                "SELECT COUNT(*) AS n FROM vocabulary WHERE user_id = ?",
                (user_id,),
            ).fetchone()["n"]
            vocab_rows = conn.execute(
                "SELECT word, context, first_seen FROM vocabulary WHERE user_id = ? ORDER BY first_seen DESC LIMIT 20",
                (user_id,),
            ).fetchall()
            mistakes = conn.execute(
                "SELECT pattern, count FROM mistakes WHERE user_id = ? ORDER BY count DESC LIMIT 8",
                (user_id,),
            ).fetchall()

        def _avg(rows: list[sqlite3.Row]) -> dict[str, int]:
            if not rows:
                return {"grammar": 0, "vocabulary": 0, "pronunciation": 0, "fluency": 0, "sessions": 0}
            keys = ["grammar_score", "vocabulary_score", "pronunciation_score", "fluency_score"]
            out = {}
            for key in keys:
                values = [row[key] for row in rows if row[key] is not None]
                out[key.replace("_score", "")] = round(sum(values) / len(values)) if values else 0
            out["sessions"] = len(rows)
            return out

        buckets = {}
        for label, start in windows.items():
            chosen = [
                row
                for row in sessions
                if row["started_at"] and datetime.fromisoformat(row["started_at"]) >= start
            ]
            buckets[label] = _avg(chosen)

        trend = []
        for row in sessions[-14:]:
            trend.append(
                {
                    "date": row["started_at"][:10],
                    "mode": row["mode"],
                    "grammar": row["grammar_score"] or 0,
                    "fluency": row["fluency_score"] or 0,
                }
            )

        mode_counts = Counter(row["mode"] for row in sessions)
        return {
            "vocabulary_count": vocab_count,
            "words": [dict(row) for row in vocab_rows],
            "windows": buckets,
            "mistakes": [dict(row) for row in mistakes],
            "trend": trend,
            "favorite_modes": mode_counts.most_common(5),
            "speaking_confidence": buckets["weekly"].get("fluency", 0),
        }
