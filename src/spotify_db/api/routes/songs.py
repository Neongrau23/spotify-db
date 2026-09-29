"""Songs-Enrichment-Endpoints (APIRouter, Root-Pfade).

Dünne HTTP-Schicht über `spotify_db.db.songsdb` — die extern befüllte `songs.db`
(BPM, Key, Camelot, Audio-Features, Lyrics, Credits, Links …), verknüpft über
denselben `track_id` wie `spotify.db`. Rein lesend; fehlt die DB, antworten die
Routen mit `503`.
"""

from fastapi import APIRouter
from starlette.responses import JSONResponse

from spotify_db.api.routes.db import _clamp_int, _split_csv
from spotify_db.common.status import read_status
from spotify_db.db import songsdb

router = APIRouter()


# DEF: Songs auflisten (alle Infos, paginiert + durchsuchbar)
@router.get("/songs")
def get_songs(
    limit: str | None = None,
    offset: str | None = None,
    q: str | None = None,
    fields: str | None = None,
):
    """Songs aus der Enrichment-DB, paginiert (`limit`/`offset`), durchsuchbar (`q`)."""
    try:
        limit_val = _clamp_int(limit, 100, min_val=1, max_val=1000)
        offset_val = _clamp_int(offset, 0, min_val=0)
        data, total = songsdb.list_songs(
            limit_val,
            offset_val,
            q=q,
            fields=_split_csv(fields),
        )
        return {"data": data, "count": len(data), "total": total, "offset": offset_val}
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# DEF: Alle Infos zum aktuell laufenden Song abrufen
@router.get("/songs/current")
def get_current_song():
    """Alle Enrichment-Infos zum gerade laufenden Track (track_id aus `status.json`).

    Diese statische Route wird vor `/songs/{track_id}` registriert und daher zuerst
    gematcht. `404`, wenn kein Tracker-Status existiert, kein Track läuft, oder der
    Track noch nicht in `songs.db` steht.
    """
    try:
        status = read_status()
        if status is None:
            return JSONResponse(
                {"error": "Keine Live-Daten verfügbar. Tracker läuft nicht."}, status_code=404
            )

        track_id = (status.get("track_data") or {}).get("track_id")
        if not track_id:
            return JSONResponse({"error": "Kein aktueller Track.", "data": None}, status_code=404)

        song = songsdb.get_song(track_id)
        if song:
            return {"data": song}
        return JSONResponse({"error": "Song not found", "data": None}, status_code=404)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e), "data": None}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e), "data": None}, status_code=500)


# DEF: Alle Infos zu einem Song abrufen
@router.get("/songs/{track_id}")
def get_song(track_id: str):
    """Alle gespeicherten Enrichment-Infos zu einer Spotify-Track-ID."""
    try:
        song = songsdb.get_song(track_id)
        if song:
            return {"data": song}
        return JSONResponse({"error": "Song not found", "data": None}, status_code=404)
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e), "data": None}, status_code=503)
    except Exception as e:
        return JSONResponse({"error": str(e), "data": None}, status_code=500)
