"""Nur-Lese-Zugriff auf die externe Songs-Enrichment-DB (`songs.db`).

Diese Datenbank wird **nicht** von spotify-db geschrieben — sie wird extern befüllt
(BPM, Key, Camelot, Audio-Features, Lyrics, Credits, Links …) und ist über denselben
`track_id` mit `spotify.db` verknüpft. spotify-db liest hier ausschließlich. Die
Connection wird darum read-only (`mode=ro`) geöffnet; fehlt die Datei, melden die
Reads das sauber an die Route (statt eine leere DB anzulegen).

Spalten werden zur Laufzeit per `PRAGMA table_info` ermittelt — so liefert die
Schnittstelle automatisch „alle Infos", ohne das Schema hier doppelt zu pflegen.
"""

import sqlite3
import threading
from contextlib import contextmanager

from spotify_db.common.config import get_songs_db_path

# SECTION: - Verbindungs-Cache -

# STATE: Eine einzige, langlebige Read-only-Connection pro Prozess
_songs_conn = None
_songs_conn_lock = threading.Lock()
# STATE: Gecachte Spaltennamen der songs-Tabelle (für Feld-Allow-List + SELECT)
_songs_columns: tuple[str, ...] | None = None


# DEF: Persistente Read-only-Connection holen (Lazy Init)
def get_songs_connection():
    """Gibt die gecachte read-only songs.db-Connection zurück (Lazy Init).

    Raises:
        FileNotFoundError: Wenn die songs.db nicht existiert (extern befüllte DB).
    """
    global _songs_conn
    if _songs_conn is not None:
        return _songs_conn

    with _songs_conn_lock:
        if _songs_conn is not None:
            return _songs_conn

        db_path = get_songs_db_path()
        if not db_path or not db_path.exists():
            raise FileNotFoundError("songs.db nicht gefunden — Enrichment-DB nicht vorhanden.")

        # mode=ro: rein lesend, spotify-db schreibt diese DB nie selbst
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        _songs_conn = conn
        return _songs_conn


# DEF: Connection schließen (Cleanup)
def close_songs_connection():
    """Schließt die gecachte songs.db-Connection (idempotent)."""
    global _songs_conn, _songs_columns
    with _songs_conn_lock:
        if _songs_conn is not None:
            try:
                _songs_conn.close()
            finally:
                _songs_conn = None
                _songs_columns = None


# DEF: Gelockter Lese-Zugriff
@contextmanager
def _read_songs_lock():
    """Liefert die songs.db-Connection unter gehaltenem Lock."""
    conn = get_songs_connection()
    with _songs_conn_lock:
        yield conn


# DEF: Spaltennamen der songs-Tabelle (gecacht)
def get_columns() -> tuple[str, ...]:
    """Liefert alle Spaltennamen der `songs`-Tabelle (per PRAGMA, einmalig gecacht)."""
    global _songs_columns
    if _songs_columns is not None:
        return _songs_columns

    with _read_songs_lock() as conn:
        rows = conn.execute("PRAGMA table_info(songs)").fetchall()
    _songs_columns = tuple(row["name"] for row in rows)
    return _songs_columns


# DEF: Spaltenauswahl auf gültige Spalten eingrenzen
def _resolve_columns(fields=None) -> str:
    """Baut die SELECT-Spaltenliste; unbekannte Felder werden ignoriert (Allow-List)."""
    if not fields:
        return "*"
    valid = get_columns()
    requested = [f for f in fields if f in valid]
    return ", ".join(requested) if requested else "*"


# SECTION: - Reads -


# DEF: Einzelnen Song (alle Infos) holen
def get_song(track_id):
    """Liefert alle gespeicherten Infos zu einem Track als Dict, oder None wenn unbekannt."""
    with _read_songs_lock() as conn:
        row = conn.execute("SELECT * FROM songs WHERE track_id = ?", (track_id,)).fetchone()
    return dict(row) if row else None


# DEF: Songs auflisten (paginiert, durchsuchbar, mit Feld-Auswahl)
def list_songs(limit, offset, q=None, fields=None):
    """Liefert Songs aus der Enrichment-DB, paginiert und optional durchsucht.

    Args:
        limit: Maximale Anzahl Einträge.
        offset: Überspringt die ersten N Einträge.
        q: Optionaler Suchstring (LIKE auf title/artists/album).
        fields: Gewünschte Spalten; unbekannte werden ignoriert (Allow-List).

    Returns:
        tuple[list[dict], int]: (Einträge dieser Seite, Gesamtanzahl Treffer).
    """
    columns = _resolve_columns(fields)

    where = ""
    params: list = []
    if q:
        where = "WHERE title LIKE ? OR artists LIKE ? OR album LIKE ?"
        like = f"%{q}%"
        params = [like, like, like]

    with _read_songs_lock() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM songs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT {columns} FROM songs {where} ORDER BY saved_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [dict(row) for row in rows], total
