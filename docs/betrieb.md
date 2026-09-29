# Betrieb: Konfiguration & Deployment

Zurück zum [Index](index.md).

## Konfiguration

### Schichten

1. **Eingebaute Defaults** in `common/config.py::load_config()`.
2. **`config.json`** (Projekt-Wurzel) — überschreibt selektiv: Top-Level-Keys flach gemerged,
   das `paths`-Dictionary **tief** gemerged (die JSON muss nur abweichende Pfade nennen).
3. **`.env`** — Spotify-Credentials und die Web-Server-Einstellungen `HOST`/`PORT`/`SERVE_PATH`.

### Mechanik (`common/config.py`)

- `PROJECT_ROOT` wird per **Marker-Suche** ermittelt: von `__file__` aufwärts bis zum
  Verzeichnis mit `pyproject.toml` (Fallback: feste Parent-Zählung im src-Layout) — der Code
  funktioniert unabhängig vom CWD und vom Installationsmodus.
- `load_config()` cached das Ergebnis in `_cached_config` (auch die reinen Defaults, wenn
  `config.json` fehlt). `set_config_value(key, value)` schreibt **genau einen** Schlüssel in
  die Datei — nie die zusammengeführte Konfiguration, sonst würden alle Defaults samt Pfaden
  festgeschrieben und spätere Default-Änderungen im Code kämen nicht mehr an — und verwirft
  den eigenen Cache. ⚠️ **Andere Prozesse** sehen Änderungen an `config.json` trotzdem erst
  nach `load_config(force_reload=True)` oder Neustart.
- `get_path(key)` löst einen `paths`-Key relativ zu `PROJECT_ROOT` auf (gibt `None` bei
  unbekanntem Key). Darauf bauen alle spezialisierten Getter auf: `get_log_path`,
  `get_lock_path`, `get_api_lock_path`, `get_web_lock_path`, `get_status_path`,
  `get_data_dir`, `get_public_dir`, `get_cache_path`, `get_resync_flag_path`, `get_db_path`,
  `get_local_db_path`, `get_songs_db_path`, `get_backup_dir`. Dazu kommen
  `get_web_host`/`get_web_port` (`.env` → `config.json` → Default) und
  `get_remote_backup_config`.

### Schlüssel in `config.json`

`config.json` ist gerätespezifisch und nicht versioniert; `config.example.json` dient als
Vorlage.

| Schlüssel | Default | Wirkung |
| --- | --- | --- |
| `track_base_info` | `true` | Tracking aktiv (bei `false` liefert der Collector `None`) |
| `track_artist_details` | `true` | Genre-/Artist-Anreicherung aktiv |
| `clear_on_new_song` | `true` | Terminal bei Track-Wechsel leeren |
| `show_status_header` | `true` | Konfig-Header im Terminal |
| `show_stats` | `true` | Session-Statistik im Terminal |
| `cors_origins` | `["*"]` | erlaubte CORS-Origins der API |
| `web_host` / `web_port` | `0.0.0.0` / `15002` | Bind-Adresse des Web-Servers (per `.env` `HOST`/`PORT` überschreibbar) |
| `remote_backup` | `enabled: false` | optionaler scp-Snapshot beim Tracker-Stop (`host`, `user`, `port` (Default 22), `dest`, `identity_file`) |
| `paths.*` | `data/…` | alle Laufzeitpfade, relativ zur Projekt-Wurzel |
| `default` | – | Befehl für ein argumentloses `spotify-db` (per `--set-default`) |

### `.env`-Variablen

| Variable | Pflicht | Zweck |
| --- | --- | --- |
| `SPOTIPY_CLIENT_ID` / `SPOTIPY_CLIENT_SECRET` / `SPOTIPY_REDIRECT_URI` | ja | Spotify-OAuth |
| `HOST` / `PORT` / `SERVE_PATH` | nein | Bind-Adresse, Port und Verzeichnis des Web-Servers (Fallback: `config.json`, dann Default) |

API-Keys stehen **nicht** in der `.env` — sie liegen in der Tabelle `api_keys` in `local.db`
(siehe [datenbank.md](datenbank.md#schema)).

## Deployment

spotify-db läuft überall, wo Python ≥ 3.12 läuft — auf einem Linux-Rechner, einem
Raspberry Pi oder einem Android-Handy über [Termux](https://termux.dev). Voraussetzung ist nur,
dass das Gerät dauerhaft läuft, solange getrackt werden soll. Die API ist **lokal und im
Heimnetz** erreichbar:

- Lokal: `http://127.0.0.1:15001`
- Heimnetz: `http://<geräte-ip>:15001`

Die FastAPI/uvicorn-API bindet auf `0.0.0.0:15001` (einzelner Prozess); Auth (API-Key) liegt
vollständig in der App (ASGI-Middleware, siehe [architektur.md](architektur.md#api-interna-app-setup--gateway)).
Kein externer Tunnel, kein Reverse-Proxy — die API ist nicht aus dem Internet erreichbar.

### Plattform-Hinweise

- **Android (Termux):** `termux-wake-lock` verhindert, dass Android die Prozesse bei
  ausgeschaltetem Bildschirm schlafen legt. Für die Remote-Kopie der Backups
  `pkg install openssh`.
- **Windows:** `spotify-db --stop` beendet die Prozesse per `taskkill /F`, also hart — das
  finale Backup des Trackers beim Beenden entfällt dort (das periodische läuft trotzdem).

### Web-Server

`web/server.py` (`python -m spotify_db.web.server`, Lock `data/web.lock`) liefert ein
Verzeichnis im Heimnetz aus — **reine Standardbibliothek** (`http.server`), mit
Directory-Listing, `index.html`, MIME-Erkennung, HEAD, Trailing-Slash-Redirect (301) und
Pfad-Traversal-Schutz. Kein Auth, kein Tunnel. Bind-Adresse, Port und Verzeichnis kommen aus
`HOST`/`PORT`/`SERVE_PATH` (Default `0.0.0.0:15002`, `public/`); das Verzeichnis wird beim Start
bei Bedarf angelegt. `bin/index.html` ist eine minimale Startseite als Vorlage — nach
`public/` kopieren, sonst zeigt der Server nur das Directory-Listing.
