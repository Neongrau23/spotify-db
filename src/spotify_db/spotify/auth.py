import os

from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

from spotify_db.common.config import get_cache_path

load_dotenv()

# CONFIG: Pflicht-Variablen aus der .env (Vorlage: .env.example)
_REQUIRED_ENV_VARS = ("SPOTIPY_CLIENT_ID", "SPOTIPY_CLIENT_SECRET", "SPOTIPY_REDIRECT_URI")

# STATE: In-Memory Cache für den Auth-Manager
_auth_manager = None


# DEF: Fehlende Zugangsdaten
class SpotifyCredentialsError(RuntimeError):
    """Die Spotify-Zugangsdaten fehlen in der Umgebung bzw. `.env`.

    Eigene Exception statt spotipys `SpotifyOauthError`: dessen Meldung nennt nur
    die erste fehlende Variable und keinen Weg zur Behebung. Beim ersten Start ist
    das der häufigste Fehler überhaupt — er soll ohne Traceback verständlich sein.
    """


# DEF: Auth-Manager holen (Lazy Init)
def get_auth_manager():
    """Gibt den (lazy initialisierten, gecachten) Spotify-OAuth-Manager zurück.

    Returns:
        SpotifyOAuth: Auth-Manager mit Token-Cache unter dem konfigurierten Pfad.

    Raises:
        SpotifyCredentialsError: Wenn eine der Pflicht-Variablen fehlt.
    """
    global _auth_manager
    if _auth_manager is not None:
        return _auth_manager

    missing = [name for name in _REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        raise SpotifyCredentialsError(
            f"Spotify-Zugangsdaten fehlen: {', '.join(missing)}. "
            "`.env.example` nach `.env` kopieren und die Werte der Spotify-App eintragen "
            "(siehe README, Abschnitt Einrichtung)."
        )

    cache_path = str(get_cache_path())

    _auth_manager = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope="user-read-currently-playing user-read-playback-state user-read-recently-played user-library-read user-modify-playback-state",
        cache_path=cache_path,
    )
    return _auth_manager
