"""Spotify-Playback-Steuerung (Play/Pause-Toggle, Next, Previous, Seek, Volume).

Nutzt den gecacheten Spotify-Client aus `spotify/client.py` und setzt danach
das Resync-Flag, damit der Tracker sofort einen Re-Poll auslöst.
"""

from spotify_db.common.config import get_resync_flag_path
from spotify_db.common.status import read_status

from .client import get_spotify_client


# DEF: Resync-Flag triggern
def _trigger_resync():
    """Setzt das Resync-Flag — der Tracker re-pollt daraufhin innerhalb von 100 ms."""
    flag_path = get_resync_flag_path()
    if flag_path is None:
        return
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.touch()


# DEF: Play/Pause toggeln
def toggle():
    """Toggelt Play/Pause. Liest is_playing aus status.json um einen Spotify-Call zu sparen."""
    sp = get_spotify_client()

    data = read_status()
    is_playing = ((data.get("track_data") or {}).get("is_playing")) if data else None

    if is_playing is None:
        current = sp.currently_playing()
        is_playing = bool(current and current.get("is_playing"))

    if is_playing:
        sp.pause_playback()
    else:
        sp.start_playback()

    _trigger_resync()


# DEF: Nächster Track
def next_track():
    """Springt zum nächsten Track."""
    sp = get_spotify_client()
    sp.next_track()
    _trigger_resync()


# DEF: Vorheriger Track
def previous_track():
    """Springt zum vorherigen Track."""
    sp = get_spotify_client()
    sp.previous_track()
    _trigger_resync()


# DEF: Position im Track suchen (Seek)
def seek(position_ms: int):
    """Springt im aktuellen Track an die übergebene Position (in Millisekunden)."""
    sp = get_spotify_client()
    sp.seek_track(max(0, int(position_ms)))
    _trigger_resync()


# DEF: Aktuellen Volume aus status.json lesen (mit API-Fallback)
def _get_current_volume(sp) -> int:
    """Liest volume_percent aus status.json; fällt auf Spotify-API zurück."""
    data = read_status()
    if data:
        vol = (data.get("device") or {}).get("volume_percent")
        if vol is not None:
            return int(vol)

    # API-Fallback
    current = sp.current_playback()
    if current and current.get("device"):
        return int(current["device"].get("volume_percent", 50))
    return 50  # sicherer Standardwert


# DEF: Lautstärke setzen
def set_volume(volume_percent: int):
    """Setzt die Lautstärke auf einen absoluten Wert (0 bis 100)."""
    sp = get_spotify_client()
    clamped = max(0, min(100, int(volume_percent)))
    sp.volume(clamped)
    _trigger_resync()


# DEF: Lautstärke hoch
def volume_up(step: int = 10):
    """Erhöht die Lautstärke um `step` Prozentpunkte (Standard: 10)."""
    sp = get_spotify_client()
    current_vol = _get_current_volume(sp)
    new_vol = min(100, current_vol + max(1, int(step)))
    sp.volume(new_vol)
    _trigger_resync()


# DEF: Lautstärke runter
def volume_down(step: int = 10):
    """Verringert die Lautstärke um `step` Prozentpunkte (Standard: 10)."""
    sp = get_spotify_client()
    current_vol = _get_current_volume(sp)
    new_vol = max(0, current_vol - max(1, int(step)))
    sp.volume(new_vol)
    _trigger_resync()
