# Datenbank & Backup

SQLite-Persistenz (`db/`), Schema und Sicherung. Zurück zum [Index](index.md).

## Die drei Datenbanken

| Datei | Inhalt | In Backups? |
| --- | --- | --- |
| `spotify.db` | `tracks`, `history` | ja (lokal + Remote-Kopie) |
| `local.db` | `api_keys` — gerätespezifisch, Connection in `db/localdb.py` | nein; Keys auf jedem Gerät separat anlegen |
| `songs.db` | extern befüllte Enrichment-DB (BPM, Key, Lyrics …), über `track_id` verknüpft | nein; spotify-db liest sie nur (`db/songsdb.py`, `mode=ro`) |

Beim ersten Start der API oder der Keys-CLI wird `local.db` angelegt und vorhandene Keys aus
einer alten `spotify.db` werden einmalig migriert. Fehlt `songs.db`, antworten die
`/songs`-Endpunkte mit `503`. Das Spaltenschema steht unter [Schema](#schema).

## Verbindungsmodell (`db/database.py`)

- **Genau eine langlebige Connection pro Prozess** (`_conn`, Lazy-Init mit Double-Checked
  Locking unter `_conn_lock`), `check_same_thread=False`, `row_factory = sqlite3.Row`.
- PRAGMAs beim Öffnen: `journal_mode=WAL` (Tracker schreibt, API liest parallel),
  `foreign_keys=ON` (derzeit ohne praktische Wirkung — es sind keine FKs definiert),
  `busy_timeout=5000` (wartet bei Lock-Kontention, z. B. WAL-Checkpoint, bis 5 s statt sofort
  mit „database is locked" zu scheitern).
- **Alle** Zugriffe — auch Lesezugriffe — laufen unter `_conn_lock`, weil die API ihre
  sync-Endpoints in Starlettes Threadpool ausführt, also mehrthreadig auf der einen geteilten
  Connection arbeitet. Nach außen kapseln zwei Contextmanager den privaten Lock:
  **`read_lock()`** (Lesen; Basis aller Funktionen in `queries.py`) und **`transaction()`**
  (Schreiben mit Commit/Rollback; genutzt von `db/keys.py`). Interne Schreiboperationen nutzen
  `with _conn_lock, conn:`.
- `close_connection()` ist idempotent; aufgerufen im `lifespan` (API) bzw. `finally` (Tracker).
- Zeitstempel: `_now()` = `datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")` — **alle DB-Zeiten
  sind UTC** im Format `YYYY-MM-DD HH:MM:SS`.

## Schema

Die DDL-Statements liegen zentral in `db/models.py` (`ALL_STATEMENTS`); `initialize_db()`
führt sie der Reihe nach aus. Es ist idempotent (`CREATE TABLE IF NOT EXISTS`) und wird beim
Tracker-Start und beim API-Start aufgerufen. Das Schema von `local.db` legt
`localdb.initialize_local_db()` an (API-Start und Keys-CLI). Es gibt **keine Migrationen** —
neue Spalten erfordern Handarbeit.

**`tracks`** — ein Datensatz pro Spotify-Track (kumulierte Werte):

| Spalte | Typ | Inhalt |
| --- | --- | --- |
| `track_id` | TEXT PK | Spotify-Track-ID |
| `track_name` | TEXT | Trackname |
| `artists` | TEXT | **komma-getrennter String** (kein Array) |
| `album` | TEXT | Albumname |
| `genres` | TEXT | **komma-getrennter String** |
| `duration_ms` | INTEGER | Trackdauer |
| `release_date` | TEXT | `YYYY-MM-DD` (von Spotify, kann auch nur Jahr sein) |
| `added_at` | DATETIME | erster Eintrag in die DB (UTC) |
| `last_seen` | DATETIME | letzter Poll mit diesem Track (UTC) |
| `total_listen_ms` | INTEGER | kumulierte Hörzeit (vom Tracker inkrementell hochgezählt) |
| `album_cover_url` | TEXT | Spotify-CDN-URL |
| `artist_images` | TEXT | komma-getrennte Bild-URLs |
| `spotify_url` | TEXT | Direktlink |

**`history`** — eine Zeile pro *Wiedergabe-Session* (Track-Wechsel ⇒ neue Zeile):

| Spalte | Typ | Inhalt |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | |
| `track_id` | TEXT | Spotify-ID (bewusst **ohne** FK-Constraint) |
| `track_name`, `artists`, `album` | TEXT | denormalisiert für einfaches Lesen |
| `total_listen_ms` | INTEGER | Hörzeit **dieser einen Session** (nicht kumuliert) |
| `played_at` | DATETIME | Start der Wiedergabe (UTC) |

Indizes: `idx_history_played_at`, `idx_history_track_id`.

**`api_keys`** (in `local.db`) — trägt die Auth des Gateways, wird von keinem Endpunkt
ausgeliefert:

| Spalte | Typ | Inhalt |
| --- | --- | --- |
| `id` | INTEGER PK | Auto-Increment-ID |
| `key_hash` | TEXT UNIQUE | sha256-Hex-Hash des Keys (nie der Klartext) |
| `name` | TEXT | sprechender Name des Aufrufers (z. B. `handy`) |
| `created_at` | DATETIME | Zeitpunkt der Erstellung |
| `revoked` | INTEGER | `1` = widerrufen (Soft-Delete, greift sofort) |

**`songs`** (in `songs.db`) — extern befüllt, spotify-db liest sie nur (Endpunkte `/songs*`).
Verknüpfung zu `tracks` über
`track_id`. Die Spalten sind typisiert (`TEXT`/`INTEGER`/`REAL`), so wie die DB extern erzeugt
wird; spotify-db liest das Schema zur Laufzeit per `PRAGMA table_info` und setzt keine Spalte voraus.

| Spalte             | Typ     | Beschreibung                                                |
|--------------------|---------|-------------------------------------------------------------|
| `track_id`         | TEXT PK | Spotify-Track-ID (Verknüpfung zu `tracks`)                  |
| `title`            | TEXT    | Trackname                                                   |
| `artists`          | TEXT    | Künstler als JSON-Array-String (`["A", "B"]`)               |
| `album`            | TEXT    | Albumname                                                   |
| `release_date`     | TEXT    | Erscheinungsdatum (ISO, z.B. `2023-07-28`)                  |
| `isrc`             | TEXT    | ISRC-Code                                                   |
| `explicit`         | INTEGER | Explicit-Flag (`0`/`1`)                                     |
| `label`            | TEXT    | Label                                                       |
| `genres`           | TEXT    | Genres als JSON-Array-String                                |
| `bpm`              | REAL    | Tempo in BPM                                                |
| `key`              | TEXT    | Tonart (z.B. `G major`)                                     |
| `key_pitch_class`  | INTEGER | Tonhöhenklasse des Grundtons (0 = C … 11 = B)               |
| `key_mode`         | INTEGER | Tongeschlecht (`1` = Dur, `0` = Moll)                       |
| `camelot`          | TEXT    | Camelot-Code für Harmonic Mixing (z.B. `9B`)                |
| `duration_ms`      | INTEGER | Länge in Millisekunden                                      |
| `popularity`       | INTEGER | Spotify-Popularität (0–100)                                 |
| `acousticness`     | REAL    | Audio-Feature (0–1)                                         |
| `danceability`     | REAL    | Audio-Feature (0–1)                                         |
| `energy`           | REAL    | Audio-Feature (0–1)                                         |
| `instrumentalness` | REAL    | Audio-Feature (0–1)                                         |
| `liveness`         | REAL    | Audio-Feature (0–1)                                         |
| `speechiness`      | REAL    | Audio-Feature (0–1)                                         |
| `happiness`        | REAL    | Audio-Feature / Valence (0–1)                               |
| `loudness`         | REAL    | Lautheit in dB (z.B. `-9.0`)                                |
| `analysis`         | TEXT    | Freitext-Analyse des Tracks                                 |
| `lyrics`           | TEXT    | Lyrics als JSON-String (Sektionen/Zeilen)                   |
| `synced_lyrics`    | TEXT    | Zeitgestempelte Lyrics (nur in neueren Ständen der DB)      |
| `album_tracklist`  | TEXT    | Album-Tracklist als JSON-String                             |
| `credits`          | TEXT    | Credits als JSON-String                                     |
| `link_genius`      | TEXT    | Link zu Genius (evtl. `null`)                               |
| `link_spotify`     | TEXT    | Link zum Track auf Spotify                                  |
| `link_tunebat`     | TEXT    | Link zu Tunebat                                             |
| `link_songstats`   | TEXT    | Link zu Songstats                                           |
| `link_songbpm`     | TEXT    | Link zu SongBPM                                             |
| `saved_at`         | TEXT    | ISO-Zeitstempel der externen Speicherung                    |

## Funktionen

**Schreiben** (`database.py`):

| Funktion | Verhalten |
| --- | --- |
| `save_track_to_db(track_data, is_new_play, listen_increment_ms)` | UPSERT in `tracks` (existiert ⇒ UPDATE inkl. `total_listen_ms += increment`, sonst INSERT). Increment wird auf `[0, 30000]` geklemmt. `is_new_play=True` ⇒ neue `history`-Zeile; sonst `total_listen_ms`-UPDATE auf die Zeile mit `MAX(id)` für diese `track_id`. Rückgabe `True` **nur** beim allerersten DB-Eintrag des Tracks |

**Lesen** (`queries.py` — von API-Routen, Tracker und Collector genutzt):

| Funktion | Verhalten |
| --- | --- |
| `get_track_stats(track_id)` | `{total_listen_ms, rank}`; Rang = Anzahl Tracks mit mehr Hörzeit + 1; `{0, 0}` bei unbekannter ID. **Hot-Path** des Live-Widgets |
| `get_track_details_or_none(track_id)` | gecachte Artist-/Genre-Details als Listen (Split an `", "`), oder `None` wenn unbekannt **oder ohne Genres** — Vertragsbasis des Collector-Caches |
| `get_total_tracks_count()` / `get_total_listen_ms()` / `get_track_added_at()` | Gesamtzahlen bzw. formatiertes Erst-Hördatum |
| `list_tracks` / `get_track` / `list_history` | Queries der Lese-Endpoints (inkl. Spalten-Allow-List und Pagination) |
| `top_tracks_by_listen` / `top_genres_by_listen` / `top_artists_by_listen` / `top_tracks_by_plays` | Top-N-Queries (Genres/Artists in Python aggregiert) |
| `get_db_stats()` | Gesamt-Statistik (Shape des `/stats`-Endpoints) |
| `format_added_at` / `format_dd_hh_mm` / `format_hh_mm_ss` | Format-Helfer (`DD.MM.YYYY`, `TTd HHh MMm`, `HH:MM:SS`/`MM:SS`) |

**Keys** (`keys.py` — Auth-Daten, via `transaction()`/`read_lock()`):

| Funktion | Verhalten |
| --- | --- |
| `create_key(name)` | erzeugt `secrets.token_urlsafe(32)`, speichert sha256-Hash, gibt den Klartext **einmalig** zurück |
| `verify_key(presented)` | Hash-Lookup `WHERE key_hash = ? AND revoked = 0` — Hot-Path des Gateways |
| `has_active_keys()` | Fail-closed-Check (existiert mindestens ein aktiver Key?) |
| `list_keys()` / `revoke_key(id\|name)` | Übersicht (Hash nur als Präfix) bzw. Soft-Delete |

## Backup & Remote-Kopie

`db/backup.py`. Einen Cloud-Sync gibt es nicht.

### Konstanten

| Konstante | Wert | Bedeutung |
| --- | --- | --- |
| `_LOCAL_BACKUP_KEEP` | 7 | lokale Snapshots, die aufbewahrt werden |
| `BACKUP_INTERVAL_S` (in `tracker.py`) | 1800 | Backup-Intervall des Trackers |

`_backup_lock` (`threading.Lock`, non-blocking acquire) verhindert, dass sich Backup-Läufe
überlappen (z. B. periodischer + manueller Lauf) — der zweite Aufruf wird übersprungen, nicht
gewartet.

### Backup-Zyklus — `run_backup()`

Ablauf (auch hinter `spotify-db --backup`):

1. **Integritätsprüfung** `PRAGMA integrity_check` auf der Live-DB — schlägt sie fehl, wird
   abgebrochen (kein Backup einer kaputten DB).
2. **WAL-Checkpoint** `PRAGMA wal_checkpoint(FULL)` — schreibt das WAL-Journal in die
   Hauptdatei zurück (verhindert unkontrolliertes WAL-Wachstum); Fehler nicht kritisch.
3. **Snapshot** via `sqlite3.backup()`-API (online-sicher, konsistent trotz laufendem
   Schreiber) nach `<backup_dir>/spotify_<YYYYMMDD_HHMMSS>.db` (UTC-Timestamp).
4. **Rotation** auf 7 Dateien.

Rückgabe `True`, sobald der Snapshot gelang.

### Remote-Kopie — `push_to_remote()`

Läuft nur beim Beenden des Trackers (im `finally`-Block nach dem finalen Backup) und nur, wenn
`remote_backup.enabled` in `config.json` gesetzt ist (Keys siehe
[betrieb.md](betrieb.md#schlüssel-in-configjson)). Ablauf: TCP-Prüfung des SSH-Ports →
online-sicherer Snapshot nach `data/.remote_push.db` → `ssh mkdir -p <dest>` → `scp` nach
`<dest>/spotify.db` (überschreibt). Setzt passwortlose SSH-Key-Auth voraus (`BatchMode=yes`);
braucht keinen Mount und kein Skript auf der Gegenseite. Best effort — Fehler werden geloggt,
verhindern den Stop aber nie.
