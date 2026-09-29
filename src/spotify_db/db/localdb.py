"""SQLite-Connection für local.db — gerätespezifische Daten.

Enthält aktuell nur die `api_keys`-Tabelle. Liegt bewusst getrennt von spotify.db,
damit die Key-Hashes nicht in Backups und Kopien der Hör-Datenbank landen.
"""

import sqlite3
import threading
from contextlib import contextmanager

from spotify_db.common.config import get_local_db_path
from spotify_db.db import models

# SECTION: - Verbindungs-Cache -

# STATE: Eine einzige, langlebige Connection pro Prozess
_local_conn = None
_local_conn_lock = threading.Lock()


# DEF: Persistente Connection holen (Lazy Init)
def get_local_connection():
    """Gibt die gecachte local.db-Connection zurück und initialisiert sie bei Bedarf."""
    global _local_conn
    if _local_conn is not None:
        return _local_conn

    with _local_conn_lock:
        if _local_conn is not None:
            return _local_conn

        db_path = get_local_db_path()
        if db_path is None:
            raise RuntimeError("Lokaler DB-Pfad nicht konfiguriert.")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local_conn = conn
        return _local_conn


# DEF: Connection schließen (Cleanup)
def close_local_connection():
    """Schließt die gecachte local.db-Connection (idempotent)."""
    global _local_conn
    with _local_conn_lock:
        if _local_conn is not None:
            try:
                _local_conn.close()
            finally:
                _local_conn = None


# DEF: Gelockter Lese-Zugriff
@contextmanager
def read_local_lock():
    """Liefert die local.db-Connection unter gehaltenem Lock."""
    conn = get_local_connection()
    with _local_conn_lock:
        yield conn


# DEF: Gelockter Schreib-Zugriff (mit Commit)
@contextmanager
def local_transaction():
    """Liefert die local.db-Connection unter Lock als Transaktion (Commit/Rollback)."""
    conn = get_local_connection()
    with _local_conn_lock, conn:
        yield conn


# SECTION: - Datenbank-Initialisierung -


# DEF: Schema-Init + einmalige Migration (idempotent)
def initialize_local_db():
    """Legt das Schema in local.db an und migriert api_keys aus spotify.db falls nötig."""
    conn = get_local_connection()
    with _local_conn_lock, conn:
        for statement in models.LOCAL_STATEMENTS:
            conn.execute(statement)
        _migrate_api_keys_from_spotify_db(conn)


# DEF: Einmalige Migration (läuft nur wenn local.db noch leer ist)
def _migrate_api_keys_from_spotify_db(local_conn):
    """Kopiert api_keys aus spotify.db → local.db, sofern local.db noch keine hat."""
    row = local_conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()
    if row[0] > 0:
        return  # Schon migriert oder eigene Keys vorhanden

    from spotify_db.common.config import get_db_path

    spotify_path = get_db_path()
    if not spotify_path or not spotify_path.exists():
        return

    try:
        old = sqlite3.connect(str(spotify_path))
        old.row_factory = sqlite3.Row
        # Tabelle existiert möglicherweise nicht in alten spotify.db-Versionen
        rows = old.execute("SELECT key_hash, name, created_at, revoked FROM api_keys").fetchall()
        old.close()
    except Exception:
        return

    if not rows:
        return

    local_conn.executemany(
        "INSERT OR IGNORE INTO api_keys (key_hash, name, created_at, revoked) VALUES (?, ?, ?, ?)",
        [(r["key_hash"], r["name"], r["created_at"], r["revoked"]) for r in rows],
    )
    print(f"{len(rows)} API-Key(s) aus spotify.db nach local.db migriert.")
