"""Tabellen-Schemata (DDL) der SQLite-Datenbank.

Alle CREATE-Statements sind idempotent (IF NOT EXISTS); `database.initialize_db()`
führt sie der Reihe nach aus. Neue Tabellen/Indizes gehören hierher, nicht in
den Anwendungscode.
"""

# MARK: - Tracks (Haupttabelle) -
TRACKS_DDL = """
    CREATE TABLE IF NOT EXISTS tracks (
        track_id TEXT PRIMARY KEY,
        track_name TEXT,
        artists TEXT,
        album TEXT,
        genres TEXT,
        duration_ms INTEGER,
        release_date TEXT,
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_seen DATETIME,
        total_listen_ms INTEGER DEFAULT 0,
        album_cover_url TEXT,
        artist_images TEXT,
        spotify_url TEXT
    )
"""

# MARK: - History (Hör-Sessions) -
HISTORY_DDL = """
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id TEXT,
        track_name TEXT,
        artists TEXT,
        album TEXT,
        total_listen_ms INTEGER DEFAULT 0,
        played_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
"""

HISTORY_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_history_played_at ON history(played_at)",
    "CREATE INDEX IF NOT EXISTS idx_history_track_id ON history(track_id)",
)

# MARK: - API-Keys (Auth der HTTP-API) -
# Liegt in local.db (nicht in spotify.db), weil Keys gerätespezifisch sind.
# Gespeichert wird nur der sha256-Hash, nie der Klartext. Zurückziehen = revoked-Flag
# setzen (Soft-Delete, Audit bleibt erhalten). Verwaltung über `python -m spotify_db.api.keys`.
API_KEYS_DDL = """
    CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_hash TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        revoked INTEGER NOT NULL DEFAULT 0
    )
"""

# STATE: Reihenfolge ist relevant (Indizes nach ihren Tabellen)
# Nur spotify.db-Tabellen — api_keys gehören in local.db (siehe localdb.py)
ALL_STATEMENTS = (
    TRACKS_DDL,
    HISTORY_DDL,
    *HISTORY_INDEXES,
)

# STATE: Schema für local.db (gerätespezifisch, nicht in Backups)
LOCAL_STATEMENTS = (API_KEYS_DDL,)
