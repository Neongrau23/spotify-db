# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Spotify-Hörverlauf-Tracker (plattformunabhängig, im Dauerbetrieb u. a. unter Termux/Android):
pollt den laufenden Song über die Spotify Web API, speichert Tracks, Verlauf und Hörzeit in
SQLite und stellt alles über eine API-Key-geschützte FastAPI-REST-API im Heimnetz bereit. Die ausführliche Doku liegt in `docs/` (Einstieg:
`docs/index.md`) — dieses File fasst nur zusammen, was man vor jeder Änderung wissen muss.

## Befehle

```sh
pip install -e .            # Pflicht vor allem anderen: ohne Installation kein `python -m spotify_db.…`
ruff check .                # Lint (Konfiguration in ruff.toml)
ruff format .               # Formatierung

spotify-db --run [--tracker|--api|--web]    # Prozesse losgelöst starten (ohne Flag: alle drei)
spotify-db --stop [--tracker|--api|--web]
spotify-db --status                         # PID-Status + aktueller Track
spotify-db --backup                         # Backup-Zyklus sofort ausführen

python -m spotify_db.spotify.tracker        # Tracker im Vordergrund (Debugging, loggt dann auch auf stdout)
python -m spotify_db.api.app                # API im Vordergrund (0.0.0.0:15001, Swagger unter /docs)
python -m spotify_db.api.keys create --name <aufrufer>   # API-Key anlegen (auch: list, revoke)
```

**Es gibt keine Test-Suite.** Verifiziert wird manuell: Tracker im Vordergrund starten, API mit
`curl -H "X-API-Key: …" http://127.0.0.1:15001/<pfad>` abfragen.

Ein lokal vorhandenes `.venv/` stammt ggf. vom Zielgerät (z. B. Termux, aarch64/Android) und
ist dann auf einem x86-Entwicklungsrechner nicht ausführbar. `ruff` und `python3 -m py_compile`
funktionieren ohne venv; für alles, was die installierten Abhängigkeiten braucht
(`pip install -e .`, Prozesse starten), ein venv der eigenen Plattform anlegen. `uv.lock` ist
versioniert und plattformübergreifend — nach Änderungen an den Abhängigkeiten in
`pyproject.toml` mit `uv lock` neu erzeugen.

## Architektur

**Drei unabhängige Prozesse** — Tracker (`spotify/tracker.py`), API (`api/app.py`) und ein
optionaler statischer Web-Server (`web/server.py`, reine Standardbibliothek). `main.py` ist nur
Prozessmanager: startet sie als losgelöste Subprozesse und beendet sich; kein Supervisor, kein
Neustart. Beim Tracker-Stop wird bis zu 60 s gewartet, damit das finale Backup im
`finally`-Block durchläuft.

**Kommunikation ausschließlich über das Dateisystem** (`data/`) — kein Socket, kein RPC:

- `spotify.db` (SQLite, WAL): Tracker schreibt, API liest parallel.
- `status.json`: Live-Zustand, vom Tracker pro Poll und sekündlich geschrieben (atomar über
  `common/status.py`). **Single source of truth** für `/live` — die API fragt Spotify nie selbst
  nach dem Fortschritt. Leser prüfen `api_error` **vor** den Track-Feldern.
- `resync.flag`: jeder Playback-Befehl der API legt es an; der Tracker prüft es im 100-ms-Takt
  und pollt sofort neu.
- `*.lock`: PID-Dateien für Einzelinstanz und `--status`.

Alle Pfade laufen über die Getter in `common/config.py` (`get_db_path()`, `get_status_path()` …)
— **nie hartkodieren**; per `paths` in `config.json` ist jeder Pfad verlegbar.

**Tracker-Loop** (Details: `docs/tracker.md`):

- Poll alle 5 s bei Wiedergabe, 15 s bei Pause/Idle, dazwischen 100-ms-Ticks. Jede Sekunde wird
  `status.json` mit **interpoliertem** `progress_ms` neu geschrieben (Dead Reckoning). Nur
  `progress_ms` ändert sich zwischen Polls — diese Trennung von Poll- und interpolierten Werten
  beim Editieren erhalten.
- Hörzeit ist die **tatsächlich verstrichene Zeit** zwischen zwei Polls und wird nur
  gutgeschrieben, wenn beim vorherigen Poll derselbe Track spielte; `save_track_to_db` klemmt sie
  auf 30 s. Dadurch ist das Poll-Intervall von der Hörzeit-Logik entkoppelt.
- Neue `history`-Zeile nur bei Track-*Wechsel*; derselbe Track verlängert die letzte Zeile.
- Der Collector nutzt die DB als Genre-Cache: der Spotify-Artists-Call fällt pro Track nur einmal
  an. Spotify-Fehler außer 429 werden als `SpotifyApiError` geworfen, damit „Spotify lehnt ab"
  von „nichts läuft" unterscheidbar bleibt.

**Datenbanken** (Details: `docs/datenbank.md`):

- `spotify.db` (`tracks`, `history`) — wird gesichert (lokal ×7 alle 30 min, optional scp beim
  Tracker-Stop).
- `local.db` (`api_keys`, nur sha256-Hashes) — gerätespezifisch, nicht im Backup.
- `songs.db` — extern befüllte Enrichment-DB, nur read-only gelesen; fehlt sie, liefern
  `/songs*` 503.
- Eine langlebige Connection pro Prozess; **jeder** Zugriff läuft unter dem Lock — lesen über
  `read_lock()`, schreiben über `transaction()` (`db/database.py`), weil die API sync-Handler im
  Threadpool ausführt.
- DDL zentral in `db/models.py` (`CREATE … IF NOT EXISTS`). **Keine Migrationen** — neue Spalten
  in bestehenden DBs erfordern Handarbeit.
- Alle Zeitstempel UTC als `YYYY-MM-DD HH:MM:SS`. `tracks.artists`/`genres` sind
  komma-getrennte Strings, in `songs.db` dagegen JSON-Arrays.

**API** (Details: `docs/architektur.md`, Referenz: `docs/api.md`):

- `app.py` ist nur Kompositions-Wurzel. **Neue Endpoints gehören in einen Router unter
  `api/routes/`**, die Handler bleiben dünn und delegieren an `db/` bzw. `spotify/`.
- Handler sind sync `def` und reine try/except-Hüllen (Erfolg → `dict`, Fehler → `JSONResponse`).
  Query-Parameter als `str | None = None`, lenient geparst (`_clamp_int`/`_split_csv`) — ungültige
  Werte fallen still auf den Default zurück, kein 422.
- Auth als HTTP-Middleware in `gateway.py` (Key-Lookup pro Request, kein Cache); CORS wird zuletzt
  hinzugefügt, damit auch 401/503 CORS-Header tragen.
- Nur **ein** uvicorn-Worker — geteilte Connection und Single-PID-Lock setzen das voraus.
- ⚠️ **Pfade und Antwort-Shapes sind ein stabiler Vertrag.** Routen-Änderungen immer in
  `docs/api.md` nachziehen.

**Abhängigkeitsrichtung:** `api/` und `spotify/` rufen nach unten in `db/` und `common/`; diese
importieren nie nach oben. Alle `__init__.py` bleiben leer (damit z. B. die Keys-CLI nicht
FastAPI mitlädt).

## Konfiguration

Drei Schichten: eingebaute Defaults in `common/config.py` → `config.json` (gerätespezifisch,
nicht versioniert; Vorlage `config.example.json`) → `.env` (Spotify-OAuth-Credentials,
Web-Server-`HOST`/`PORT`/`SERVE_PATH`; Vorlage `.env.example`). API-Keys stehen **nicht** in der
`.env`, sondern in `local.db`. Gerätespezifische Werte (Hosts, Nutzer, Pfade) gehören nur in die
nicht versionierten Dateien, in den Vorlagen stehen Platzhalter. Andere laufende Prozesse sehen
Änderungen an `config.json` erst nach Neustart.

## Conventions

Verbindliche Fassung (von `docs/entwicklung.md` hierher verlinkt):

- Kommentare, Docstrings, Log- und UI-Texte **Deutsch**; Bezeichner **Englisch**.
- Dateinamen strikt lowercase.
- Navigationsmarker erhalten und bei neuem Code im selben Stil setzen: `# SECTION: - Titel -`
  gliedert Module, `# DEF: <Kurztitel>` steht direkt über Funktionen (in Modulen, die ihn
  nutzen, auch bei neuen Funktionen setzen), `# MARK: - … -` gliedert innerhalb von Funktionen;
  dazu `# STATE:` (Zustand/Cache), `# CONFIG:` (Konstanten/Einstellungen) und
  `# BRIDGE:` (Zugriffe nach außen, z. B. Dateisystem).
- Kommentare erklären das *Warum*, nicht das Was.
- Docstrings im Google-Stil — ruff (`D`) erzwingt sie auf jeder Funktion und Klasse.
- Doku in `docs/`: jeder Fakt steht an genau einer Stelle, andere Dateien verlinken dorthin;
  keine Datei-Inventare pflegen. Bekannte Eigenheiten und Fallstricke stehen in
  `docs/entwicklung.md` — vor Änderungen an Artists/Genres, History oder Volume dort nachlesen.
