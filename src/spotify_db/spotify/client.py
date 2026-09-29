"""Spotify Client Management.

Bietet einen zentralen, gecacheten Spotify-Client.
"""

import spotipy

from .auth import get_auth_manager

_spotify_client = None


# DEF: Spotify-Client holen (Lazy Init)
def get_spotify_client():
    """Gibt eine persistente Instanz des Spotify-Clients zurück (Lazy Initialization)."""
    global _spotify_client
    if _spotify_client is None:
        auth_manager = get_auth_manager()
        _spotify_client = spotipy.Spotify(auth_manager=auth_manager)
    return _spotify_client
