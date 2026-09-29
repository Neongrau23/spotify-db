"""Live-Endpoint (APIRouter) — liest die vom Tracker geschriebene status.json.

Bewusst eigene Datei: `/live` ist die einzige Misch-Route (Status-Datei als
Quelle, plus zwei DB-Anreicherungen); `routes/db.py` bleibt dadurch rein
DB-gebunden. Spotify wird hier NICHT abgefragt — die status.json ist die
einzige Quelle für Live-Daten (Dead Reckoning macht der Tracker).
"""

from fastapi import APIRouter
from starlette.responses import JSONResponse

from spotify_db.common.status import read_status
from spotify_db.db import queries

router = APIRouter()


# DEF: Live-Status abrufen
@router.get("/live")
def get_live_stats():
    """Aktueller Tracker-Live-Status aus der vom Tracker geschriebenen Status-JSON."""
    try:
        data = read_status()
        if data is None:
            return JSONResponse(
                {"error": "Keine Live-Daten verfügbar. Tracker läuft nicht."}, status_code=404
            )

        track_data = data.get("track_data") or {}

        # MARK: - "Erstes Mal gehört" (added_at aus der DB, anhand der track_id) -
        added_at_str = ""
        track_id = track_data.get("track_id")
        if track_id:
            added_at_str = queries.get_track_added_at(track_id)

        # MARK: - Gesamte Hörzeit über alle Tracks (dd.hh.mm für die Topbar) -
        db_listen_ms = queries.get_total_listen_ms()
        db_listen_time = queries.format_dd_hh_mm(db_listen_ms)

        duration_ms = track_data.get("duration_ms", 0)
        minutes = (duration_ms // 1000) // 60
        seconds = (duration_ms // 1000) % 60
        duration_str = f"{minutes:02}:{seconds:02}"

        # MARK: - Live-Progress (sekündlich vom Tracker per Dead Reckoning aktualisiert) -
        progress_ms = track_data.get("progress_ms", 0)
        if duration_ms:
            progress_ms = min(progress_ms, duration_ms)
        p_minutes = (progress_ms // 1000) // 60
        p_seconds = (progress_ms // 1000) % 60
        progress_str = f"{p_minutes:02}:{p_seconds:02}"

        total_listen_ms = data.get("total_listen_ms", 0)

        # MARK: - Wie oft gehört (Hörzeit / Tracklänge, in 0,1-Schritten) -
        play_count = round(total_listen_ms / duration_ms, 1) if duration_ms else 0.0

        # MARK: - Nutzerprofil (vom Tracker beim Start in den Status geschrieben) -
        profile = data.get("profile") or {}

        return {
            "username": profile.get("username", ""),
            "profile_image_url": profile.get("profile_image_url", ""),
            "is_new": data.get("is_new_in_db", False),
            "session_total": data.get("session_total", 0),
            "session_unique": data.get("session_unique", 0),
            "db_total": data.get("db_total", 0),
            "song": track_data.get("name", "Unbekannt"),
            "artists": track_data.get("artists", []),
            "genres": track_data.get("genres", []),
            "album_cover_url": track_data.get("album_cover_url", ""),
            "duration": duration_str,
            "duration_ms": duration_ms,
            "progress": progress_str,
            "progress_ms": progress_ms,
            "is_playing": track_data.get("is_playing", False),
            "total_listen_ms": total_listen_ms,
            "total_listen_time": queries.format_hh_mm_ss(total_listen_ms),
            "play_count": play_count,
            "listen_rank": data.get("track_rank", 0),
            "added_at": added_at_str,
            "db_listen_ms": db_listen_ms,
            "db_listen_time": db_listen_time,
            # `{"status", "message"}`, wenn Spotify den letzten Poll abgelehnt hat —
            # sonst null. Trennt "Spotify antwortet nicht" von "es läuft nichts".
            "api_error": data.get("api_error"),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
