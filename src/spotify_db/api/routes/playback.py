"""Playback-Steuerungs-Endpoints unter `/control/*` (APIRouter).

Dünne HTTP-Schicht über `spotify_db.spotify.playback`: jede Aktion proxyt zu Spotify
und triggert dort das Resync-Flag für den Tracker. Registriert wird der Router
in `spotify_db.api.app`; die API-Key-Auth läuft dort zentral über das Gateway.

`toggle/next/previous` sind sync `def` (der blockierende Spotify-Call läuft in
Starlettes Threadpool). `seek` muss den Request-Body lesen und ist daher `async`;
der blockierende Spotify-Call wird per `run_in_threadpool` ausgelagert.
"""

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from spotify_db.spotify.playback import next_track, previous_track, seek, toggle

router = APIRouter(prefix="/control")


# DEF: Playback-Toggle
@router.post("/toggle")
def control_toggle():
    """Play/Pause toggeln."""
    try:
        toggle()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# DEF: Nächster Song
@router.post("/next")
def control_next():
    """Zum nächsten Track springen."""
    try:
        next_track()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# DEF: Vorheriger Song
@router.post("/previous")
def control_previous():
    """Zum vorherigen Track springen."""
    try:
        previous_track()
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# DEF: Song-Position suchen (Seek)
@router.post("/seek")
async def control_seek(request: Request):
    """Position im aktuellen Track setzen. Body: { "position_ms": 60000 }."""
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        position_ms = payload.get("position_ms")
        if position_ms is None:
            return JSONResponse({"error": "position_ms required"}, status_code=400)
        await run_in_threadpool(seek, int(position_ms))
        return {"ok": True}
    except (ValueError, TypeError):
        return JSONResponse({"error": "position_ms must be an integer"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
