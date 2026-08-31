from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from voice_grammar_agent.tutor import EnglishTutor

STATIC_DIR = Path(__file__).resolve().parent / "static"
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "lumen-web" / "dist"
AUDIO_STORE: dict[str, Path] = {}


@lru_cache(maxsize=1)
def get_tutor() -> EnglishTutor:
    return EnglishTutor()


def _store_audio(path: str | None) -> str | None:
    if not path:
        return None
    audio_id = uuid.uuid4().hex
    AUDIO_STORE[audio_id] = Path(path)
    return f"/api/audio/{audio_id}"


def create_app() -> FastAPI:
    app = FastAPI(title="AI English Tutor")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        spa = FRONTEND_DIR / "index.html"
        index = spa if spa.exists() else STATIC_DIR / "index.html"
        return HTMLResponse(
            index.read_text(encoding="utf-8"),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/catalog")
    def catalog():
        return get_tutor().catalog()

    @app.post("/api/rewrite")
    def rewrite(text: str = Form(...)):
        spoken = (text or "").strip()
        if not spoken:
            raise HTTPException(status_code=400, detail="No words yet.")
        return {"original": spoken, "corrected": spoken}

    @app.post("/api/speak")
    def speak(text: str = Form(...)):
        spoken = (text or "").strip()
        if not spoken:
            raise HTTPException(status_code=400, detail="Nothing to speak.")
        from voice_grammar_agent.avatars import get_avatar

        cues = get_tutor()._tts.synthesize_cues(spoken, voice=get_avatar("maya").voice)
        return {"audio_url": _store_audio(cues.path)}

    @app.post("/api/session/start")
    def start_session(mode: str = Form(default="free"), avatar: str = Form(default="maya")):
        try:
            result = get_tutor().start_session(mode, avatar)
            result["audio_url"] = _store_audio(result.pop("audio_path", None))
            return result
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/tutor/turn")
    async def tutor_turn(
        text: str = Form(default=""),
        session_id: str = Form(...),
        mode: str = Form(default="free"),
        avatar: str = Form(default="maya"),
        spoken: str = Form(default="true"),
        audio: Optional[UploadFile] = File(default=None),
    ):
        audio_path = None
        try:
            if audio is not None and audio.filename:
                suffix = Path(audio.filename).suffix or ".webm"
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(await audio.read())
                    audio_path = tmp.name
            result = await asyncio.to_thread(
                get_tutor().turn,
                session_id=session_id,
                text=text,
                mode=mode,
                avatar_id=avatar,
                spoken=spoken.lower() != "false",
                audio=audio_path,
            )
            result["audio_url"] = _store_audio(result.pop("audio_path", None))
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/session/end")
    def end_session(session_id: str = Form(...)):
        return get_tutor().end_session(session_id)

    @app.get("/api/progress")
    def progress(user_id: str = "local"):
        return get_tutor().progress(user_id)

    @app.websocket("/ws/tutor")
    async def tutor_socket(ws: WebSocket):
        await ws.accept()
        try:
            while True:
                raw = await ws.receive_json()
                kind = str(raw.get("type") or "")
                try:
                    if kind == "start":
                        result = await asyncio.to_thread(
                            get_tutor().start_session,
                            raw.get("mode") or "free",
                            raw.get("avatar") or "maya",
                            raw.get("user_id") or "local",
                        )
                        result["audio_url"] = _store_audio(result.pop("audio_path", None))
                        await ws.send_json({"ok": True, "type": "session", **result})
                    elif kind == "turn":
                        audio_path = None
                        b64 = raw.get("audio_b64")
                        if b64:
                            await ws.send_json({"type": "status", "status": "transcribing"})
                            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                                tmp.write(base64.b64decode(b64))
                                audio_path = tmp.name
                        else:
                            await ws.send_json({"type": "status", "status": "thinking"})
                        result = await asyncio.to_thread(
                            get_tutor().turn,
                            session_id=raw["session_id"],
                            text=raw.get("text") or "",
                            mode=raw.get("mode") or "free",
                            avatar_id=raw.get("avatar") or "maya",
                            user_id=raw.get("user_id") or "local",
                            spoken=str(raw.get("spoken", True)).lower() != "false",
                            audio=audio_path,
                        )
                        result["audio_url"] = _store_audio(result.pop("audio_path", None))
                        await ws.send_json({"ok": True, "type": "turn", **result})
                    else:
                        await ws.send_json({"ok": False, "type": "error", "detail": "Unknown message"})
                except KeyError:
                    await ws.send_json({"ok": False, "type": "error", "detail": "session_id is required"})
                except ValueError as exc:
                    await ws.send_json({"ok": False, "type": "error", "detail": str(exc)})
                except Exception as exc:
                    await ws.send_json({"ok": False, "type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return
        except json.JSONDecodeError:
            await ws.close(code=1003)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return HTMLResponse(status_code=204)

    @app.get("/api/audio/{audio_id}")
    def audio(audio_id: str) -> FileResponse:
        audio_file = AUDIO_STORE.get(audio_id)
        if audio_file is None or not audio_file.exists():
            raise HTTPException(status_code=404, detail="Audio not found")
        return FileResponse(audio_file, media_type="audio/mpeg")

    assets = FRONTEND_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="spa-assets")

    return app


app = create_app()


def launch(host: str = "0.0.0.0", port: int = 7860) -> None:
    import uvicorn

    print(f"Open the AI English Tutor at http://127.0.0.1:{port}")
    uvicorn.run(
        "voice_grammar_agent.web_app:app",
        host=host,
        port=port,
        reload=True,
        log_level="info",
    )
