"""SQLite-Persistenz für den Spotify-Tracker.

Hält genau eine Connection pro Prozess (WAL), serialisiert Schreibzugriffe per Lock.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from spotify_db.common.config import get_db_path
from spotify_db.db import models

# SECTION: - Verbindungs-Cache -

# STATE: Eine einzige, langlebige Connection pro Prozess
_conn = None
_conn_lock = threading.Lock()

# CONFIG: Obergrenze für die pro Schreibvorgang gutgeschriebene Hörzeit (ms).
# Die tatsächlich verstrichene Hörzeit berechnet der Tracker aus der Zeit zwischen
# zwei Polls; dieser Deckel kappt unplausible Sprünge (System-Standby, Prozess-
# Neustart), damit kein riesiger Block fälschlich auf einen Track addiert wird.
MAX_LISTEN_INCREMENT_MS = 30000


# DEF: Persistente Connection holen (Lazy Init)
def get_connection():
    """Gibt die gecachte SQLite-Connection zurück und initialisiert sie bei Bedarf."""
    global _conn
    if _conn is not None:
        return _conn

    with _conn_lock:
        if _conn is not None:
            return _conn

        db_path = get_db_path()
        if db_path is None:
            raise RuntimeError("DB-Pfad nicht konfiguriert.")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Wartet bei Lock-Kontention (z.B. WAL-Checkpoint) bis zu 5s, statt sofort
        # mit "database is locked" zu scheitern.
        conn.execute("PRAGMA busy_timeout=5000")
        _conn = conn
        return _conn


# DEF: Connection schließen (Cleanup)
def close_connection():
    """Schließt die gecachte Connection (idempotent)."""
    global _conn
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None


# DEF: Gelockter Lese-Zugriff (für API-Threadpool-Threads)
@contextmanager
def read_lock():
    """Liefert die Connection unter gehaltenem `_conn_lock`.

    Die API läuft auf FastAPI/uvicorn; die sync-Endpoints werden in Starlettes
    Threadpool ausgeführt, also weiterhin mehrthreadig auf einer einzigen, geteilten
    Connection. Roh-Queries müssen daher denselben Lock halten wie die übrigen Helfer,
    sonst können gleichzeitige Requests sich Cursor/Connection-Zustand zerschießen.
    """
    conn = get_connection()
    with _conn_lock:
        yield conn


# DEF: Gelockter Schreib-Zugriff (mit Commit)
@contextmanager
def transaction():
    """Liefert die Connection unter `_conn_lock` als Transaktion (Commit/Rollback).

    Pendant zu `read_lock()` für Schreibzugriffe von außerhalb dieses Moduls
    (z.B. die Key-Verwaltung) — kapselt den privaten Lock, statt ihn zu exportieren.
    """
    conn = get_connection()
    with _conn_lock, conn:
        yield conn


# SECTION: - Datenbank Initialisierung -


# DEF: Schema-Init (idempotent)
def initialize_db():
    """Legt alle Tabellen und Indizes an (DDL aus `models.py`). Mehrfach-Aufruf ist sicher."""
    conn = get_connection()
    with _conn_lock, conn:
        for statement in models.ALL_STATEMENTS:
            conn.execute(statement)


# SECTION: - Hilfsfunktion -


def _now():
    """Gibt den aktuellen Zeitpunkt im DB-Format zurück (UTC, ISO-ähnlich)."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


# SECTION: - Track-Schreibvorgänge -


# DEF: Track + History UPSERT
def save_track_to_db(track_data, is_new_play=False, listen_increment_ms=0):
    """Speichert/aktualisiert Track-Metadaten und ggf. History-Eintrag.

    Args:
        track_data: dict mit Spotify-Feldern (track_id, name, artists, ...).
        is_new_play: True wenn ein neuer Song begonnen hat.
        listen_increment_ms: Tatsächlich seit dem letzten Poll verstrichene Hörzeit
            (vom Tracker berechnet). Wird auf [0, MAX_LISTEN_INCREMENT_MS] geklemmt,
            bevor sie aufaddiert wird.

    Returns:
        bool: True wenn der Track das erste Mal überhaupt in der DB landet.
    """
    if not track_data or not track_data.get("track_id"):
        return False

    artists_str = ", ".join(track_data.get("artists", []))
    genres_str = ", ".join(track_data.get("genres", []))
    album_str = track_data.get("album", "")
    now = _now()
    listen_increment = max(0, min(int(listen_increment_ms), MAX_LISTEN_INCREMENT_MS))

    conn = get_connection()

    with _conn_lock, conn:
        # MARK: - Track UPSERT -
        existing = conn.execute(
            "SELECT added_at FROM tracks WHERE track_id = ?",
            (track_data["track_id"],),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE tracks SET
                    track_name = ?, artists = ?, album = ?, genres = ?,
                    duration_ms = ?, release_date = ?, last_seen = ?,
                    total_listen_ms = total_listen_ms + ?, album_cover_url = ?,
                    artist_images = ?, spotify_url = ?
                WHERE track_id = ?
                """,
                (
                    track_data.get("name"),
                    artists_str,
                    album_str,
                    genres_str,
                    track_data.get("duration_ms"),
                    track_data.get("release_date"),
                    now,
                    listen_increment,
                    track_data.get("album_cover_url", ""),
                    track_data.get("artist_images", ""),
                    track_data.get("spotify_url"),
                    track_data["track_id"],
                ),
            )
            is_first_time_ever = False
        else:
            conn.execute(
                """
                INSERT INTO tracks (
                    track_id, track_name, artists, album, genres, duration_ms,
                    release_date, added_at, last_seen, total_listen_ms,
                    album_cover_url, artist_images, spotify_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track_data["track_id"],
                    track_data.get("name"),
                    artists_str,
                    album_str,
                    genres_str,
                    track_data.get("duration_ms"),
                    track_data.get("release_date"),
                    now,
                    now,
                    listen_increment,
                    track_data.get("album_cover_url", ""),
                    track_data.get("artist_images", ""),
                    track_data.get("spotify_url"),
                ),
            )
            is_first_time_ever = True

        # MARK: - History -
        if is_new_play:
            conn.execute(
                """
                INSERT INTO history (track_id, track_name, artists, album, total_listen_ms, played_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    track_data["track_id"],
                    track_data.get("name"),
                    artists_str,
                    album_str,
                    listen_increment,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE history SET total_listen_ms = total_listen_ms + ?
                WHERE id = (SELECT MAX(id) FROM history WHERE track_id = ?)
                """,
                (listen_increment, track_data["track_id"]),
            )

    return is_first_time_ever
