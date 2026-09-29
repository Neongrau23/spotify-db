"""Holt und reichert Metadaten zum aktuell spielenden Spotify-Track an."""

import logging
import time

from spotipy.exceptions import SpotifyException

from spotify_db.common.config import load_config
from spotify_db.db.queries import get_track_details_or_none

from .client import get_spotify_client

logger = logging.getLogger(__name__)

# SECTION: - Fehler-Vertrag -


# DEF: Abgelehnter Spotify-Aufruf
class SpotifyApiError(Exception):
    """Spotify hat den Aufruf abgelehnt (alles außer Rate-Limiting).

    Bewusst eine Exception statt eines `None`-Rückgabewerts: `None` bedeutet im
    Collector "es läuft gerade nichts". Würde ein 403/401 dasselbe liefern,
    landete ein harter Fehlerzustand als "Kein Track aktiv" in status.json und
    die Ursache stünde nur im Log.

    Attributes:
        status: HTTP-Status von Spotify, oder None bei nicht-HTTP-Fehlern.
        message: Klartext-Grund ohne den spotipy-Rahmen (URL, Code).
    """

    def __init__(self, message: str, status: int | None = None):
        """Setzt Klartext-Grund und optionalen HTTP-Status."""
        super().__init__(message)
        self.status = status
        self.message = message


# DEF: spotipy-Fehlermeldung auf den Klartext-Grund kürzen
def _short_api_message(e: SpotifyException) -> str:
    """Zieht den lesbaren Grund aus einer mehrzeiligen spotipy-Fehlermeldung.

    spotipy baut `msg` aus URL und Grund, getrennt durch einen Zeilenumbruch —
    für Status-Anzeige und CLI interessiert nur die letzte Zeile.

    Args:
        e (SpotifyException): Die Ausnahme von spotipy.

    Returns:
        str: Der Grund als Einzeiler.
    """
    raw = (e.msg or str(e)).strip()
    tail = raw.splitlines()[-1].strip()
    return tail or raw


# SECTION: - Spotify Datenabruf -


# DEF: Aktuellen Track abrufen
def fetch_current_track():
    """Holt Metadaten zum aktuell spielenden Spotify-Track und ergänzt diese bei Bedarf."""
    # CONFIG: Laden der Systemeinstellungen
    config = load_config()

    # Prüfung, ob die Basis-Informationen überhaupt erwünscht sind
    if not config.get("track_base_info", True):
        return None

    # BRIDGE: Abruf des gecacheten, persistenten Spotify-Clients
    sp = get_spotify_client()

    try:
        # BRIDGE: API-Call für aktuell gespielten Song
        current = sp.currently_playing()
        if not current or not current["item"]:
            return None

        # MARK: - Basisdaten Extraktion -
        track = current["item"]
        track_id = track["id"]
        release_date = track["album"].get("release_date", "")

        # STATE: Initiale Basisdaten-Struktur vorbereiten
        data = {
            "track_id": track_id,
            "name": track["name"],
            "artists": [a["name"] for a in track["artists"]],
            "album": track["album"]["name"],
            "release_date": release_date,
            "progress_ms": current.get("progress_ms", 0),
            "duration_ms": track.get("duration_ms", 0),
            "spotify_url": track["external_urls"].get("spotify"),
            "is_playing": current.get("is_playing", False),
            "genres": [],
            "album_cover_url": "",
            "artist_images": "",
        }

        # Album-Cover laden falls vorhanden
        if track["album"]["images"]:
            data["album_cover_url"] = track["album"]["images"][0]["url"]

        # MARK: - Service-Cache & Genre-Details -
        # Wenn weiterführende Artist-Details abgeschaltet sind, hier abbrechen
        if not config.get("track_artist_details", True):
            return data

        # BRIDGE: DB als Genre-Cache abfragen (kein Spotify-Call); spart den Artists-Call
        db_details = get_track_details_or_none(track_id)
        if db_details:
            # STATE: Daten aus dem Service-Cache in das aktuelle Dictionary einpflegen
            data["genres"] = db_details.get("genres", [])
            data["artist_images"] = db_details.get("artist_images", "")
            if db_details.get("artists"):
                data["artists"] = db_details["artists"]
            return data

        # MARK: - Erweiterte Künstler-Daten -
        # BRIDGE: API-Call für erweiterte Künstler-Daten (hauptsächlich für Genres)
        artist_ids = [a["id"] for a in track["artists"]]
        artists_data = sp.artists(artist_ids)

        # STATE: Hilfsvariablen für Aggregation von Listen und Sets
        all_genres = set()
        artist_images_list = []
        official_artist_names = []

        for artist_info in artists_data["artists"]:
            real_name = artist_info.get("name")
            official_artist_names.append(real_name)
            all_genres.update(artist_info.get("genres", []))
            if artist_info.get("images"):
                img_url = artist_info["images"][0]["url"]
                artist_images_list.append(img_url)

        # STATE: Finale Komplettierung des Data-Dictionaries
        data["artists"] = official_artist_names
        data["genres"] = sorted(all_genres)
        data["artist_images"] = ", ".join(artist_images_list)

        return data

    # MARK: - Fehlerbehandlung -
    except SpotifyException as e:
        if e.http_status == 429:
            # STATE: Rate-Limiting Zustand erreicht
            retry_after = int(e.headers.get("Retry-After", 5))
            logger.warning(f"Rate Limit von Spotify erreicht! Warte {retry_after} Sekunden...")

            # BRIDGE: System-Sleep triggern, um die API zu entlasten
            time.sleep(retry_after)
            # Rate-Limiting ist ein normaler Zwischenzustand, kein Fehlerzustand:
            # der nächste Poll holt den Track nach.
            return None
        raise SpotifyApiError(_short_api_message(e), e.http_status) from e
    except Exception as e:
        raise SpotifyApiError(f"Allgemeiner Fehler beim Abrufen der Spotify-Daten: {e}") from e


# DEF: Nutzerprofil abrufen
def fetch_user_profile():
    """Holt Anzeigename und Profilbild-URL des angemeldeten Spotify-Nutzers.

    Account-Daten (gerätegleich, stabil) — wird einmalig beim Tracker-Start geholt
    und in die status.json geschrieben. `display_name`/`images` liefert `/me` ohne
    Sonder-Scope; ein leeres Bild-Array bleibt einfach als leere URL.

    Returns:
        dict | None: `{"username", "profile_image_url", "spotify_url", "user_id"}`
        oder None bei einem Fehler.
    """
    # BRIDGE: Gecacheten, persistenten Spotify-Client holen
    sp = get_spotify_client()

    try:
        # BRIDGE: API-Call für das eigene Profil (/me)
        me = sp.current_user()
        if not me:
            return None

        images = me.get("images") or []
        return {
            "username": me.get("display_name") or "",
            "profile_image_url": images[0]["url"] if images else "",
            "spotify_url": (me.get("external_urls") or {}).get("spotify", ""),
            "user_id": me.get("id", ""),
        }
    except SpotifyException as e:
        # Kein Raise: das Profil ist Beiwerk (Anzeigename/Bild). Liegt die Ursache
        # tiefer (Token tot, App gesperrt), meldet sie der erste Poll als Fehlerzustand.
        logger.warning(f"Nutzerprofil nicht abrufbar ({e.http_status}): {_short_api_message(e)}")
        return None
    except Exception as e:
        logger.warning(f"Fehler beim Abrufen des Spotify-Nutzerprofils: {e}")
        return None
