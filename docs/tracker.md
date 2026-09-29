# Tracker & Spotify-Schicht

Der Poll-Loop-Prozess und die Module, über die er (und die API) mit Spotify spricht.
Zurück zum [Index](index.md).

## Tracker (`spotify/tracker.py`)

Einstieg: `python -m spotify_db.spotify.tracker` (so startet ihn auch `main.py`); direkt im
Vordergrund lauffähig für Debugging.

### Start-Sequenz

1. **SIGTERM-Handler** wird auf Modulebene registriert (`sys.exit(0)`), damit `--stop` den
   `finally`-Block (finales Backup, Connection-Close, Lock-Aufräumen) auslöst.
2. **Logging:** in `tracker.log` über einen `RotatingFileHandler` (2 MB × 4, Format
   `%(asctime)s - %(levelname)s - %(message)s`). Ein zusätzlicher StreamHandler auf stdout
   kommt **nur** bei `sys.stdout.isatty()` dazu (Vordergrund-Debugging) — im Hintergrund
   stünde sonst jede Zeile doppelt im Log. spotipys eigener Logger steht auf `CRITICAL`,
   weil der Tracker jede `SpotifyException` selbst dedupliziert loggt.
3. **Lock-Prüfung:** existiert `tracker.lock` mit lebendiger PID ⇒ Abbruch („läuft schon").
   Verwaiste Locks werden bereinigt.
4. `database.initialize_db()` (idempotent), Session-Zähler initialisieren, Nutzerprofil
   einmalig per `fetch_user_profile()` holen, dann eigene PID in `tracker.lock` schreiben.
   Fehlen dabei die Spotify-Zugangsdaten (`SpotifyCredentialsError` aus `auth.py`), bricht
   der Tracker mit einer Meldung ab, welche Variablen fehlen — noch bevor ein Lock entsteht.

### Poll-Zyklus (äußere Schleife)

Pro Iteration:

1. **`fetch_current_track()`** — Snapshot von Spotify (oder `None`, wenn nichts läuft).
   `poll_real_time = time.time()` wird unmittelbar danach festgehalten.
2. **`now_playing.txt`** aktualisieren (`"Song - ErsterArtist"`, sonst `"Inaktiv"`).
3. **Hörzeit-Increment berechnen** (elapsed-basiert, s. u.).
4. **DB-Schreiben:**
   - *Track-Wechsel* (`track_id` ≠ letzte bekannte ID): `save_track_to_db(..., is_new_play=True)`
     ⇒ neue History-Zeile; Session-Zähler (`tracks_since_start`, ggf. `session_new_songs`,
     `db_total_count`) hochzählen; Terminal-Anzeige (`print_track_display`).
   - *Gleicher Track*: `save_track_to_db(..., is_new_play=False)` ⇒ nur Metadaten/Hörzeit
     aktualisieren, Hörzeit der **letzten** History-Zeile dieses Tracks erhöhen.
5. **Poll-Zustand merken** (`last_poll_real_time`, `last_poll_track_id`, `last_poll_is_playing`)
   — die Basis für das Increment des *nächsten* Polls.
6. **Wartezeit** via `common/timer.py::calculate_wait_time()`: **5 s bei Wiedergabe, 15 s bei
   Idle/Pause**.
7. **Track-Stats** aus der DB holen (`get_track_stats`: Gesamthörzeit + Rang) und
   **`status.json` schreiben** (vollständiger Snapshot).
8. **Periodisches Backup:** alle `BACKUP_INTERVAL_S` (30 min, Konstante in `tracker.py`) wird
   `run_backup()` in einem **Daemon-Hintergrund-Thread** gestartet, damit der Tick-Loop nicht
   blockiert. Reine Crash-Versicherung — beim Beenden läuft ohnehin ein finales Backup.
9. **Innerer Tick-Loop** (s. u.).

### Innerer Tick-Loop: Dead Reckoning + Resync

Die Wartezeit ist **kein** einzelnes `sleep`, sondern `wait_time × 10` Ticks à 100 ms:

- **Jeder Tick (100 ms):** existiert `resync.flag`, wird es gelöscht und der Loop sofort
  verlassen ⇒ nächster Poll startet unmittelbar. So bekommt die UI nach einem
  Playback-Befehl innerhalb von ~100 ms frische Daten.
- **Jeder 10. Tick (1 s):** `status.json` wird mit einem **interpolierten** Fortschritt neu
  geschrieben: `interp_ms = poll_progress_ms + (now − poll_real_time) × 1000`, gedeckelt auf
  `duration_ms`. Bei Pause bleibt der Poll-Wert stehen. **Nur `progress_ms` ändert sich
  zwischen Polls** — alle anderen Felder bleiben der Poll-Snapshot. Diese Trennung zwischen
  *Poll-Werten* und *interpolierten Werten* ist beim Editieren zwingend zu erhalten.
- **Nur Windows:** Tastatur-Polling über `msvcrt.kbhit()`: Taste `!` toggelt
  `show_status_header`, Taste `i` toggelt `show_stats` (beides wird sofort in `config.json`
  gespeichert und die Anzeige neu gezeichnet). Unter Linux, macOS und Termux gibt es keine
  Tastatursteuerung.

### Hörzeit-Berechnung (elapsed-basiert)

⚠️ Kernregel: Hörzeit ist **kein fixer Block pro Poll**, sondern die tatsächlich verstrichene
Zeit zwischen zwei Polls:

```python
listen_increment_ms = int((poll_real_time - last_poll_real_time) * 1000)
```

Gutgeschrieben wird sie **nur**, wenn beim *vorherigen* Poll derselbe Track bereits
**spielte** (`last_poll_is_playing` und `last_poll_track_id == current_track_id`).
Konsequenzen:

- Track-Wechsel ⇒ Increment 0 (das Intervall gehörte noch dem alten Track-Kontext, wird aber
  bewusst verworfen).
- Pause→Resume ⇒ Increment 0 für den ersten Poll nach Resume.
- Ein durch `resync.flag` ausgelöster **früher Re-Poll bläht die Hörzeit nicht auf**
  (es wird ja nur echte verstrichene Zeit gezählt).
- Das Poll-Intervall in `calculate_wait_time` ist dadurch **entkoppelt** von der
  Hörzeit-Logik und kann gefahrlos geändert werden.
- `save_track_to_db` klemmt das Increment zusätzlich auf `[0, MAX_LISTEN_INCREMENT_MS]`
  (30 000 ms, `database.py`) — fängt System-Standby und Prozess-Neustarts ab, damit kein
  riesiger Block auf einen Track addiert wird.

### Fehlerbehandlung & Shutdown

- **Spotify lehnt ab** (`SpotifyApiError`, z. B. 403 ohne Premium, 401, 5xx): eigener Zweig.
  Der Fehler landet als `api_error = {status, message}` in `status.json`, `track_data` wird
  `null`, `now_playing.txt` zeigt „Inaktiv", dann wartet der Tracker `API_ERROR_WAIT_S`
  (60 s). Gleiche Fehler werden nur einmal geloggt; beim ersten erfolgreichen Poll wird
  `api_error` wieder `null`.
- **Jede andere Exception** im Loop wird geloggt, `status.json` wird mit
  `next_scan_time = now + 15 s` und den letzten bekannten Werten neu geschrieben, dann 15 s
  Pause und weiter — der Tracker stirbt nicht an einzelnen Fehlern.
- **Shutdown** (SIGTERM oder Ctrl-C): der `finally`-Block führt aus: finales `run_backup()`
  (best effort), optional `push_to_remote()` (scp-Kopie), `database.close_connection()`,
  `tracker.lock` löschen.

### `status.json`-Schema

Atomar geschrieben: erst in `status.json.<pid>.tmp`, dann ersetzt — gekapselt in
`common/status.py::write_status()`; die API (liest über `read_status()`) kann nie eine halb
geschriebene Datei erwischen. Der Tracker definiert die Felder, `common/status.py` definiert
Pfad, Atomarität und Fehlerverhalten.

| Feld | Inhalt |
| --- | --- |
| `next_scan_time` | Unix-Timestamp des nächsten geplanten Polls |
| `start_time` | Unix-Timestamp des Tracker-Starts |
| `session_total` | Anzahl Track-Wechsel seit Start (inkl. Wiederholungen) |
| `session_unique` | davon erstmals überhaupt in der DB gelandet |
| `db_total` | Gesamtzahl Tracks in der DB |
| `is_new_in_db` | ob der aktuelle Track brandneu in der DB ist |
| `track_data` | kompletter Collector-Snapshot (s. [`collector.py`](#collectorpy--datenabruf-mit-genre-cache)) oder `null` |
| `total_listen_ms` | kumulierte Hörzeit des aktuellen Tracks (DB) |
| `track_rank` | Hörzeit-Rang des aktuellen Tracks (1 = meistgehört) |
| `profile` | Spotify-Nutzerprofil (`username`, `profile_image_url`, `spotify_url`, `user_id`) oder `null` |
| `api_error` | `{status, message}`, wenn Spotify Anfragen ablehnt, sonst `null` |

⚠️ Leser (`--status`, `/live`) prüfen `api_error` **vor** den Track-Feldern — sonst erscheint
ein abgelehnter Aufruf als „Kein Track aktiv".

## Spotify-Schicht (`spotify/`)

### `auth.py` — OAuth

Lazy-Singleton `SpotifyOAuth` (spotipy). Credentials aus `.env`
(`SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI`; fehlt eine, wirft
`get_auth_manager()` `SpotifyCredentialsError` mit allen fehlenden Namen), Token-Cache in
`data/.spotify_cache` (Pfad via `get_cache_path()`). Erster Lauf öffnet den Browser zur
Zustimmung; danach läuft die Token-Erneuerung automatisch über den Refresh-Token.

Scopes:
`user-read-currently-playing`, `user-read-playback-state`, `user-read-recently-played`,
`user-library-read`, `user-modify-playback-state`.

### `client.py` — Client

Lazy-Singleton `spotipy.Spotify(auth_manager=…)` — eine Instanz pro Prozess, von Collector
und Playback geteilt.

### `collector.py` — Datenabruf mit Genre-Cache

`fetch_current_track()` macht **maximal zwei** Spotify-Calls:

1. `sp.currently_playing()` — immer. Kein Item ⇒ `None` (nichts läuft).
2. `sp.artists(artist_ids)` — **nur**, wenn der Track noch nicht angereichert in der DB
   liegt: zuerst wird `queries.get_track_details_or_none(track_id)` gefragt; liefert die DB
   Genres, werden Genres/Artist-Images/offizielle Artist-Namen von dort übernommen und der
   Artists-Call entfällt. **Die DB fungiert damit als Genre-Cache** — pro Track fällt der
   Artists-Call genau einmal an (beim ersten Hören).

Rückgabe-Dictionary (`track_data`):

| Feld | Quelle |
| --- | --- |
| `track_id`, `name`, `album`, `release_date`, `duration_ms`, `spotify_url` | `currently_playing().item` |
| `artists` | Liste der Künstlernamen (bei Cache-Treffer aus der DB) |
| `progress_ms`, `is_playing` | `currently_playing()` (Top-Level) |
| `album_cover_url` | erstes (größtes) Album-Image |
| `genres` | sortierte Vereinigungsmenge der Genres **aller** beteiligten Artists |
| `artist_images` | komma-getrennter String der jeweils ersten Artist-Bild-URL |

Konfigurations-Gates: `track_base_info: false` ⇒ Funktion gibt sofort `None` zurück (der
Tracker behandelt das wie „nichts spielt"). `track_artist_details: false` ⇒ nur Basisdaten,
keine Genre-Anreicherung.

Fehlerbehandlung: HTTP 429 ⇒ schläft `Retry-After` Sekunden und gibt `None` zurück
(Rate-Limiting ist ein Zwischenzustand). **Jeder andere Fehler** wird als
`SpotifyApiError(message, status)` geworfen, damit der Tracker „Spotify lehnt ab" von „es
läuft nichts" unterscheiden kann.

`fetch_user_profile()` holt einmalig beim Tracker-Start Anzeigename und Profilbild (`/me`).
Fehler ergeben hier bewusst nur `None` — das Profil ist Beiwerk; ein tieferes Problem meldet
der erste Poll.

### `playback.py` — Playback-Steuerung

Dünne Wrapper um den Spotipy-Client. **Jede** Aktion endet mit `_trigger_resync()` (touch auf
`resync.flag`), damit der Tracker sofort re-pollt:

| Funktion | Verhalten |
| --- | --- |
| `toggle()` | liest `is_playing` zuerst per `read_status()` aus `status.json` (spart einen Spotify-Call); Fallback: `currently_playing()`. Dann `pause_playback()` bzw. `start_playback()` |
| `next_track()` / `previous_track()` | direkte Weiterleitung |
| `seek(position_ms)` | `seek_track(max(0, int(ms)))` |
| `set_volume(percent)` | auf 0–100 geklemmt |
| `volume_up(step=10)` / `volume_down(step=10)` | liest aktuelle Lautstärke per `read_status()` (Schlüssel `device.volume_percent`), Fallback `current_playback()`, Notfall-Default 50 |

Über die HTTP-API sind nur toggle/next/previous/seek erreichbar; zum Volume-Pfad siehe
[Fallstricke](entwicklung.md#bekannte-eigenheiten--fallstricke).
