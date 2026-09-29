"""DB-Lese- und Stats-Endpoints (APIRouter, Root-Pfade).

Dünne HTTP-Schicht: jede Route ist nur try/except um einen Aufruf nach
`spotify_db.db.queries` — die Antwort-Shapes definieren die Query-Funktionen.
Registriert wird der Router in `spotify_db.api.app`; die API-Key-Auth läuft
dort zentral über das Gateway.

Erfolg gibt ein plain `dict` zurück (FastAPI serialisiert), Fehler eine
`JSONResponse` mit explizitem Status — so bleibt der Antwort-Vertrag exakt.
"""

from fastapi import APIRouter, Query
from starlette.responses import JSONResponse

from spotify_db.db import queries

router = APIRouter()


# SECTION: - Hilfsfunktionen -


# DEF: Rohwert lenient als Integer parsen (clampt in den erlaubten Bereich)
def _clamp_int(raw: str | None, default: int, min_val: int = 0, max_val: int | None = None) -> int:
    """Parst einen Query-Rohwert als Integer; bei ungültigem Wert wird der Default verwendet.

    Bewusst lenient (kein 422): ungültige Eingaben fallen still auf den Default zurück.
    """
    try:
        val = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        val = default
    val = max(val, min_val)
    if max_val is not None:
        val = min(val, max_val)
    return val


# DEF: Kommagetrennten Rohwert als Liste parsen
def _split_csv(raw: str | None) -> list[str] | None:
    """Parst einen kommagetrennten Query-Rohwert als bereinigte Liste (None wenn leer)."""
    if not raw:
        return None
    values = [v.strip() for v in raw.split(",") if v.strip()]
    return values or None


# SECTION: - DB-Lese-Endpoints -


# DEF: Liste aller Tracks abrufen
@router.get("/tracks")
def get_tracks(genre: str | None = None, fields: str | None = None):
    """Liefert Tracks, optional gefiltert nach Genre und/oder mit Feld-Auswahl."""
    try:
        data = queries.list_tracks(genres=_split_csv(genre), fields=_split_csv(fields))
        return {"data": data, "count": len(data)}
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# DEF: Verlauf abrufen (History), paginiert und filterbar
@router.get("/history")
def get_history(
    limit: str | None = None,
    offset: str | None = None,
    track_id: str | None = None,
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
):
    """Hör-Sessions, paginiert und nach Track-ID oder Zeitraum filterbar."""
    try:
        limit_val = _clamp_int(limit, 100, min_val=1, max_val=1000)
        offset_val = _clamp_int(offset, 0, min_val=0)
        data, total = queries.list_history(
            limit_val,
            offset_val,
            track_id=track_id,
            from_date=from_date,
            to_date=to_date,
        )
        return {"data": data, "count": len(data), "total": total, "offset": offset_val}
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# DEF: Einzelnen Track abrufen
@router.get("/track/{track_id}")
def get_track(track_id: str):
    """Vollständige Track-Daten für eine Spotify-ID."""
    try:
        track = queries.get_track(track_id)
        if track:
            return {"data": track}
        return JSONResponse({"error": "Track not found", "data": None}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e), "data": None}, status_code=500)


# SECTION: - Top-N-Endpoints -


# DEF: Top-N Tracks (nach Hörzeit)
@router.get("/top10/listen")
def get_top_listen(n: str | None = None):
    """Top-N Tracks nach Gesamthörzeit. Default: 10, max: 100 (via ?n=)."""
    try:
        data = queries.top_tracks_by_listen(_clamp_int(n, 10, min_val=1, max_val=100))
        return {"data": data, "count": len(data)}
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# DEF: Top-N Genres (nach Hörzeit)
@router.get("/top10/genres")
def get_top_genres(n: str | None = None):
    """Top-N Genres nach aggregierter Hörzeit. Default: 10, max: 100 (via ?n=)."""
    try:
        data = queries.top_genres_by_listen(_clamp_int(n, 10, min_val=1, max_val=100))
        return {"data": data, "count": len(data)}
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# DEF: Top-N Artists (nach Hörzeit)
@router.get("/top/artists")
def get_top_artists(n: str | None = None):
    """Top-N Künstler nach aggregierter Hörzeit. Default: 10, max: 100 (via ?n=)."""
    try:
        data = queries.top_artists_by_listen(_clamp_int(n, 10, min_val=1, max_val=100))
        return {"data": data, "count": len(data)}
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# DEF: Top-N Tracks (nach Wiedergabe-Anzahl)
@router.get("/top/plays")
def get_top_plays(n: str | None = None):
    """Top-N Tracks nach Anzahl der Wiedergaben (aus der History). Default: 10, max: 100 (via ?n=)."""
    try:
        data = queries.top_tracks_by_plays(_clamp_int(n, 10, min_val=1, max_val=100))
        return {"data": data, "count": len(data)}
    except Exception as e:
        return JSONResponse({"error": str(e), "data": [], "count": 0}, status_code=500)


# SECTION: - DB-Stats-Endpoints (Hot-Path) -


# DEF: Allgemeine DB-Statistiken
@router.get("/stats")
def stats():
    """Gesamt-Statistik der Datenbank."""
    try:
        return queries.get_db_stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# DEF: Statistiken für einen Track
@router.get("/tracks/{track_id}/stats")
def track_stats(track_id: str):
    """Hörzeit + Rang für einen einzelnen Track (kombiniert)."""
    try:
        return queries.get_track_stats(track_id)
    except Exception as e:
        return JSONResponse({"error": str(e), "total_listen_ms": 0, "rank": 0}, status_code=500)


# DEF: Details für einen Track (Cached)
@router.get("/tracks/{track_id}/details")
def track_details(track_id: str):
    """Gespeicherte Artist-/Genre-Details. 404 wenn unbekannt / ohne Genres."""
    try:
        details = queries.get_track_details_or_none(track_id)
        if details is None:
            return JSONResponse({"error": "no cached details"}, status_code=404)
        return details
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
