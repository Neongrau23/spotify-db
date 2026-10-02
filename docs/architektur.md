# Architektur

Gesamtbild, Prozessmanager und der Dateisystem-Vertrag zwischen den Prozessen.
Zurück zum [Index](index.md).

## Gesamtarchitektur & Datenfluss

Drei **vollständig unabhängige Prozesse**: Tracker und API kommunizieren ausschließlich über
das Dateisystem und eine gemeinsame SQLite-Datei — es gibt **keinen** Socket, kein RPC und
keine Queue zwischen ihnen. Der dritte, optionale Prozess liefert statische Dateien aus
(`web/server.py`, siehe [betrieb.md](betrieb.md#web-server)) und hat mit den anderen beiden
nichts zu tun.

```text
                       ┌────────────────────────────────────────────────┐
                       │                 Spotify Web API                │
                       └───────▲────────────────────────────▲───────────┘
                               │ Poll (5s/15s)              │ Playback-Befehle
                               │                            │
┌──────────────────────────────┴───────────┐   ┌────────────┴─────────────────────────────┐
│  Tracker  (spotify_db.spotify.tracker)   │   │ API (spotify_db.api.app, FastAPI :15001) │
│                                          │   │                                          │
│  • fetch_current_track()                 │   │  • liest SQLite (read_lock)              │
│  • schreibt SQLite (tracks, history)     │   │  • liest status.json (/live)             │
│  • schreibt status.json (sekündlich)     │   │  • proxyt Playback-Befehle an Spotify    │
│  • schreibt tmp/now_playing.txt          │   │  • setzt resync.flag nach jedem Befehl   │
│  • beobachtet resync.flag (100-ms-Takt)  │   │                                          │
│  • lokales Backup (30-min-Intervall)     │   │                                          │
└───────────────┬──────────────────────────┘   └───────────────▲──────────────────────────┘
                │                                              │
                ▼          gemeinsames Dateisystem             │
   ┌───────────────────────────────────────────────────────────┴───────┐
   │  spotify.db (WAL)   status.json   resync.flag   *.lock   *.log    │
   └───────────────────────────────────────────────────────────────────┘
                │
                ├─► alle 30 min + beim Beenden: backups/spotify_<TS>.db (×7)
                └─► beim Beenden (optional): scp-Kopie auf einen anderen Rechner
```

Zentrale Entwurfsentscheidungen:

- **Filesystem-IPC statt Netzwerk:** Tracker und API können unabhängig voneinander starten,
  abstürzen und neu gestartet werden. Der Kontrakt sind die Dateien in `data/`
  (siehe [Laufzeitdateien](#laufzeitdateien--filesystem-ipc-data)).
- **SQLite im WAL-Modus:** erlaubt einen Schreiber (Tracker) und beliebig viele Leser (API)
  gleichzeitig auf derselben Datei.
- **Dead Reckoning:** die API fragt Spotify nie selbst nach dem Fortschritt; der Tracker
  interpoliert `progress_ms` sekündlich zwischen den echten Polls und schreibt ihn in
  `status.json` (siehe [tracker.md](tracker.md#innerer-tick-loop-dead-reckoning--resync)).
- **Resync-Flag:** Playback-Befehle der API erzeugen eine Touch-Datei, die der Tracker im
  100-ms-Takt prüft — so ist der Live-Status nach Play/Pause/Next sofort (statt erst nach
  bis zu 15 s) aktuell.
- **Rein lokale Datenhaltung:** kein Cloud-Sync. Gesichert wird per lokalem Snapshot und
  optional per scp-Kopie beim Beenden (siehe [datenbank.md](datenbank.md#backup--remote-kopie)).

### Paketstruktur

src-Layout unter `src/spotify_db/`, nach Domänen geschnitten:

| Paket | Rolle |
| --- | --- |
| `db/` | Persistenz, kein HTTP — Connection, DDL, Queries, api_keys, Backup |
| `spotify/` | Spotify-Domäne, kein HTTP — Auth, Client, Collector, Playback, Tracker-Loop |
| `api/` | Liefer-Schicht: FastAPI-App, Gateway (Auth + CORS), Keys-CLI, dünne Router |
| `cli/` | Auswertungs-Befehle der CLI (`stats`, `top`, `history`), liest über `db/queries.py` |
| `common/` | geteilte Helfer: Config, `status.json`-Vertrag, Terminal-Anzeige, Timer |
| `web/` | statischer Web-Server (reine Standardbibliothek) |

Abhängigkeitsrichtung: `api`, `cli` und `spotify/tracker` rufen nach unten in `db/` und
`common/`; `db/` und `common/` importieren nie nach oben. Alle `__init__.py` sind leere Paket-Marker —
bewusst ohne Importe, damit z. B. die Keys-CLI nicht das FastAPI-Setup mitlädt.

## Prozessmanager `main.py`

`src/spotify_db/main.py` ist **ausschließlich Prozessmanager** — er enthält keinerlei
Tracking- oder API-Logik; die [Auswertungs-Befehle](#auswertungen-clistatspy) registriert er
nur als Unterbefehle und reicht sie an `cli/stats.py` weiter. Jedes `--run`-Ziel wird als
losgelöster Subprozess gestartet (`start_new_session=True` unter POSIX, `CREATE_NO_WINDOW`
unter Windows), danach beendet sich `main.py` sofort. Er **überwacht die Kinder nicht** (kein Supervisor, kein Neustart).

### CLI-Befehle

```sh
spotify-db --run                           # Tracker + API + Web-Server starten
spotify-db --run --tracker                 # nur Tracker
spotify-db --run --api                     # nur API
spotify-db --run --web                     # nur statischer Web-Server
spotify-db --stop [--tracker|--api|--web]  # stoppen (ohne Flag: alle drei)
spotify-db --status                        # PID/Liveness aller drei + aktueller Track
spotify-db --backup                        # lokalen Backup-Zyklus sofort ausführen
spotify-db --set-default --run --tracker --api --web
spotify-db                                 # führt den gespeicherten Default aus

spotify-db stats [--period …]              # Statistik (Hörzeit, Wiedergaben, Tracks …)
spotify-db top [tracks|plays|artists|genres] [-n 10] [--period …]
spotify-db history [-n 20] [--period …]    # letzte Wiedergaben
```

Ohne `--tracker`/`--api`/`--web` betreffen `--run` und `--stop` immer **alle drei** Komponenten.
Die Auswertungs-Befehle lassen sich nicht mit diesen Flags kombinieren, wohl aber als Default
speichern (`spotify-db --set-default stats --period today`).

### Interna

- **PID-Erkennung** (`_pid_alive`): POSIX über `os.kill(pid, 0)`, Windows über
  `tasklist /FI "PID eq <pid>"`. `ProcessLookupError`/`PermissionError` ⇒ tot.
- **Lock-Dateien:** `_is_running()` liest die PID aus der Lock-Datei
  (`tracker.lock` / `api.lock` / `web.lock`) und prüft Liveness. Verwaiste Locks werden bei
  `--stop` aufgeräumt.
- **Spawn** (`_spawn`): stdout/stderr jedes Kindes landen in einer eigenen Datei
  `<prozess>.out` neben der Log-Datei (`tracker.out`, `api.out`, `webserver.out`); die
  Vorgängerdatei wird bei jedem Start nach `.out.1` verschoben. **Nicht** in `tracker.log` —
  die gehört allein dem `RotatingFileHandler` des Trackers. Ist kein Log-Pfad konfiguriert,
  geht die Ausgabe nach `DEVNULL`. Gestartet werden `python -m spotify_db.spotify.tracker`,
  `python -m spotify_db.api.app` und `python -m spotify_db.web.server`.
- **Stop** (`_stop`): sendet `SIGTERM` (Windows: `taskkill /F`). Beim **Tracker** wird mit
  `wait_for_exit=True` bis zu **60 s** (600 × 0,1 s) auf das Prozessende gewartet, damit das
  finale Backup im `finally`-Block durchlaufen kann. API und Web-Server werden ohne Warten
  gestoppt. Danach wird die Lock-Datei in jedem Fall entfernt.
- **Status** (`cmd_status`): zeigt den PID-Status aller drei Prozesse; läuft der Tracker, wird
  zusätzlich `status.json` gelesen. Steht dort ein `api_error`, wird dieser gemeldet (bei 403
  mit Hinweis auf das fehlende Premium-Abo des App-Owners), sonst der aktuelle Track mit
  ▶/⏸-Symbol, Position/Dauer (`MM:SS`) und Hörzeit-Rang.
- **Backup** (`cmd_backup`): konfiguriert `logging` auf INFO und ruft
  `spotify_db.db.backup.run_backup()` auf.
- **Default-Befehl:** `--set-default` nimmt alle folgenden Argumente als Befehl
  (`argparse.REMAINDER`, dadurch klappt auch ein einzelnes Flag wie `--status`) und schreibt
  ihn per `set_config_value()` in `config.json` unter `"default"` — nur diesen Schlüssel.
  Ein argumentloses `spotify-db` parst diesen String per `shlex.split()` und führt ihn aus.
  Ohne gespeicherten Default gibt es nur einen Hinweis.
  Achtung: bereits `spotify-db --tracker` (ohne Aktion) zählt als „Aktion angegeben" und
  zeigt nur die Hilfe — der Default greift wirklich nur bei **null** Argumenten.

### Auswertungen (`cli/stats.py`)

`stats`, `top` und `history` lesen `spotify.db` direkt — die API muss nicht laufen, ein API-Key
ist nicht nötig; WAL erlaubt das Lesen parallel zum laufenden Tracker. Alle drei Befehle
nehmen dieselben Optionen:

| Option | Wirkung |
| --- | --- |
| `--period today\|week\|month\|year\|all` | Kalender-Zeitraum (Woche ab Montag); `all` bzw. ohne Angabe = gesamt |
| `--from DATUM` / `--to DATUM` | eigener Zeitraum, beide Tage inklusiv; `JJJJ-MM-TT` oder `TT.MM.JJJJ`; schließt `--period` aus |
| `-n N` | Anzahl Einträge (`top`: 10, `history`: 20) |
| `--json` | Rohdaten statt Tabelle — Shapes wie die API (`/stats`, `/top…`, `/history`) |

- **Gesamt** (ohne Zeitraum) rufen die Befehle **dieselben Queries wie die API-Endpunkte**
  (`/stats`, `/top10/listen`, `/top/plays`, `/top/artists`, `/top10/genres`, `/history`) —
  CLI und API zeigen also dieselben Zahlen. Die Datumsangaben von `stats` sind daher wie in
  `/stats` UTC-Tage.
- **Mit Zeitraum** wird `history` ausgewertet, denn `tracks.total_listen_ms` ist über die
  gesamte Zeit kumuliert; nur `history` hält fest, *wann* gehört wurde. Hörzeit zählt dabei
  zum Startzeitpunkt der Session (`played_at`); Genres kommen per Track-ID aus `tracks`.
- **Zeiträume gelten in lokaler Zeit** (Zeitzone des Systems, also `TZ` bzw.
  `/etc/localtime`). Die DB speichert UTC; die CLI rechnet die Tagesgrenzen vorher um, sonst
  endete „heute" in Deutschland um 1 bzw. 2 Uhr nachts.
- Eine **Wiedergabe** ist eine `history`-Zeile (Session) — inklusive Sessions ohne Hörzeit,
  genau wie in `/stats` und `/top/plays`. Was das für die Zahlen bedeutet, steht unter
  [Bekannte Eigenheiten](entwicklung.md#bekannte-eigenheiten--fallstricke) (Punkte 1 und 6).
- Fehlt `spotify.db`, bricht die CLI mit Hinweis ab, statt eine leere DB anzulegen. Im
  Terminal werden lange Track-Namen auf die Fensterbreite gekürzt (schmale Displays), beim
  Umleiten in Datei/Pipe nicht. `history` listet die neueste Wiedergabe unten, direkt über dem
  Prompt.

## Laufzeitdateien & Filesystem-IPC (`data/`)

Alle Pfade laufen über die Getter in `src/spotify_db/common/config.py` — **niemals
hartkodieren**. Default-Lage ist `data/`, die Datenbanken und Backups liegen in
`data/database/`; per `paths` in `config.json` lässt sich jeder Pfad verlegen.

| Datei | Schreiber | Leser | Zweck |
| --- | --- | --- | --- |
| `tracker.lock` / `api.lock` / `web.lock` | Tracker, API bzw. Web-Server (eigene PID) | `main.py`, der jeweilige Prozess selbst | „läuft"-Erkennung: Existenz + `os.kill(pid, 0)`. Jeder Prozess verweigert eine zweite Instanz seiner selbst |
| `spotify.db` (+ `-wal`, `-shm`) | Tracker (+ `initialize_db` der API) | API, Auswertungs-CLI | SQLite im WAL-Modus; Default `data/database/spotify.db` |
| `local.db` | Keys-CLI | API (Gateway) | gerätespezifisch, nur `api_keys`, **nicht** in Backups |
| `songs.db` | extern | API (`/songs*`, read-only) | Enrichment-DB, wird von spotify-db nie angelegt |
| `status.json` | Tracker (pro Poll + sekündlich, via `common/status.py`) | API (`/live`), `spotify-db --status`, `playback.toggle()` | **Single source of truth** für den Live-Status; `/live` fragt Spotify nie selbst |
| `resync.flag` | API-Prozess (`_trigger_resync` nach jedem Playback-Befehl) | Tracker (100-ms-Takt, löscht sie) | Sofortiger Re-Poll ⇒ Instant-Feedback in der UI |
| `tmp/now_playing.txt` | Tracker (pro Poll) | OBS / externe Widgets | eine Zeile `"Song - Artist"` oder `"Inaktiv"` |
| `tracker.log` (+ `.1`…`.3`) | nur der `RotatingFileHandler` des Trackers (2 MB × 4) | Mensch | Tracker-Log; kein zweiter Schreiber, sonst schreibt er nach einer Rotation in die umbenannte Datei |
| `<prozess>.out` (+ `.out.1`) | `main.py::_spawn` (stdout/stderr der Kinder) | Mensch | `print()`-Ausgaben, uvicorn-Logs, Tracebacks; pro Start neu |
| `.spotify_cache` | spotipy | spotipy | Spotify-OAuth-Token |
| `backups/spotify_<TS>.db` | `backup.py` | Mensch/Restore | lokale Snapshots (max. 7); Default `data/database/backups/` |

## API-Interna: App-Setup & Gateway

Für Entwickler — wie die App intern zusammengesetzt ist. Nutzerseitiges Verhalten (Header,
Fehlercodes, CORS) steht oben unter [Authentifizierung](api.md#authentifizierung).

### App-Setup (`api/app.py`)

Die **Kompositions-Wurzel** des API-Prozesses (`python -m spotify_db.api.app`, so startet ihn
auch `main.py`): `create_app()` baut die FastAPI-App, registriert die Router (`include_router`),
hängt das Gateway ein und definiert den Discovery-Endpoint `/`; `main()` ist der
Prozess-Einstieg. Logik gehört in die Domänen — **neue Endpoints gehören in einen der Router**
(oder einen neuen) unter `api/routes/`, nicht in `app.py`:

- `routes/db.py` → Root-Pfade (DB-Lesen + Stats; dünn → `db/queries.py`)
- `routes/live.py` → `/live` aus `status.json` (einzige Misch-Route)
- `routes/songs.py` → `/songs*` aus der read-only `songs.db` (dünn → `db/songsdb.py`)
- `routes/playback.py` → Prefix `/control` (dünn → `spotify/playback.py`)

Schema-Init und Aufräumen liegen im **`lifespan`**-Contextmanager: vor `yield` `initialize_db()`
+ `initialize_local_db()` (falls die API ohne Tracker startet), nach `yield`
`close_connection()`/`close_local_connection()`/`close_songs_connection()` — uvicorn fährt bei
SIGTERM sauber herunter und löst den Shutdown aus. `main()` schreibt die eigene PID in
`api.lock` und ruft `uvicorn.run(create_app(), …)`. Gebunden wird `0.0.0.0:15001`,
`access_log=False`; als **einzelner** Prozess (kein `workers`) — die geteilte SQLite-Connection
und das Single-PID-`api.lock` setzen das voraus.

⚠️ **Die Routen sind ein stabiler Vertrag:** interne Umbauten dürfen die nach außen sichtbaren
Pfade und Antwort-Shapes nicht ändern.

Routen-Handler sind sync `def` (DB-/Datei-Lesen läuft in Starlettes Threadpool); nur `seek` ist
`async`, weil es den Request-Body liest (der Spotify-Call läuft via `run_in_threadpool`). Die
Handler sind reine try/except-Hüllen: Erfolg → plain `dict`, Fehler → `JSONResponse` mit Status.
Query-Parameter sind als `str | None = None` deklariert und werden in `routes/db.py` über
`_clamp_int`/`_split_csv` lenient geparst — ungültige Werte fallen **still** auf den Default
zurück (kein FastAPI-422).

### Gateway (`api/gateway.py`)

`init_gateway(app)` registriert Auth und CORS app-weit:

- **Auth** als `@app.middleware("http")`-Hook auf jeder Route. Der präsentierte Key wird
  sha256-gehasht und per `verify_key` (`db/keys.py`) nachgeschlagen — über
  `run_in_threadpool`, damit der Event-Loop frei bleibt. Kein Cache ⇒ Widerruf greift sofort.
  Happy Path zuerst: eine DB-Abfrage pro Request; nur im Fehlerfall wird zusätzlich 503
  (keine aktiven Keys) vs. 401 unterschieden.
- **CORS** (`CORSMiddleware`) wird **zuletzt** hinzugefügt und liegt damit außen — so bekommen
  auch die 401/503-Antworten der Auth die `Access-Control-*`-Header. Ein späterer Rate-Limiter
  würde als zweite Middleware direkt hinter der Auth einhängen.
- **Auto-Docs:** `create_app()` überschreibt `app.openapi`, um **rein dokumentierende**
  Security-Schemata einzuhängen (`ApiKeyHeader` = `X-API-Key`, `BearerAuth` = `Bearer`,
  OR-Semantik) — dafür gibt es den „Authorize"-Button in `/docs`. Die Durchsetzung bleibt in
  der Middleware.
