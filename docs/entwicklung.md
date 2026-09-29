# Entwicklung

Werkzeuge, Konventionen und bekannte Fallstricke. Zurück zum [Index](index.md).

## Installation & Abhängigkeiten (`pyproject.toml`)

Installiert wird per `pip install -e .` (setuptools, src-Layout); das legt auch das
`spotify-db`-Konsolen-Kommando an. Ohne Installation funktioniert kein
`python -m spotify_db.…` — auf jedem Gerät zuerst installieren.

| Paket | Version | Zweck |
| --- | --- | --- |
| `fastapi` | ≥ 0.115, < 1.0 | HTTP-API (ASGI) |
| `uvicorn` | ≥ 0.30 | ASGI-Server für die API (ohne `[standard]` — uvloop/watchfiles bauen unter Termux/Android nicht) |
| `spotipy` | == 2.26.0 | Spotify Web API Client |
| `python-dotenv` | == 1.2.2 | `.env`-Laden |

## Linting & Formatierung

`ruff check .` / `ruff format .` — Konfiguration in `ruff.toml`:

- `target-version py312`, `line-length 100`, Double Quotes, Spaces, `src = ["src"]`.
- Regelsets: `E, F, I, UP, B, C4, SIM, D, N, PTH, ARG, RUF`;
  isort: `known-first-party = ["spotify_db"]`.
- **`D` (pydocstyle, Google-Konvention) erzwingt Docstrings auf jeder Funktion/Klasse**;
  ausgenommen sind Modul-Header (`D100`), `__init__.py` (`D104`) und `__init__`-Methoden
  (`D107`). `E501` ist ignoriert (Zeilenlänge macht der Formatter).
- Per-File-Ignore: `tracker.py` darf `E402` (SIGTERM-Handler bewusst vor den Imports).
- `fixable = ["ALL"]`.

**Es gibt keine Test-Suite.** Verifikation läuft manuell (Tracker im Vordergrund starten, API
mit `curl` abfragen).

## Hilfsskript `bin/export_csv.py`

Exportiert `SELECT track_name, artists, track_id FROM tracks` nach `tracks.csv` mit Header
`id,song,artist`; mehrere Artists werden von `", "` auf `";"` umgeschrieben (Format einer
externen Vorlage).

## Git-Hygiene

- `.gitignore`: Python-Artefakte, `.venv/`, `.env` (Secrets!), `data/`, `config.json`,
  `tracks.csv`, `*.lock`, `*.log`, `*.db`(`-wal`/`-shm`), IDE-Verzeichnisse, `.directory`
  (KDE-Dolphin-Metadaten). `uv.lock` ist per `!uv.lock` ausgenommen und wird versioniert.
- `.gitattributes`: `* text=auto` (LF-Normalisierung).

## Konventionen

Die verbindliche Fassung steht in [CLAUDE.md](../CLAUDE.md#conventions). Kurz: Kommentare,
Docstrings, Log- und UI-Texte **Deutsch**, Bezeichner Englisch; Dateinamen strikt lowercase;
Navigationsmarker (`SECTION:`, `DEF:`, `MARK:` …) erhalten; Kommentare erklären das *Warum*.

## Bekannte Eigenheiten & Fallstricke

Dokumentierter Ist-Zustand — teils bewusste Trade-offs, teils Kandidaten für spätere Fixes:

1. **Komma als Trennzeichen bricht bei Artist-Namen mit Komma** (z. B. „Tyler, The
   Creator"): `tracks.artists`/`genres` sind komma-getrennte Strings; alle Stellen, die sie
   wieder aufsplitten (`get_track_details_or_none`, `/top/artists`,
   `unique_artists`/`unique_genres` in `/stats`), zerlegen solche Namen falsch.
2. **`?genre=`-Filter ist Substring-Matching** (`LIKE %g%`): `rock` matcht auch `post-rock`,
   `rock'n'roll` usw.
3. **Config-Cache über Prozessgrenzen:** `set_config_value()` verwirft zwar den eigenen
   Cache, aber **andere Prozesse** sehen Änderungen an `config.json` erst nach
   `force_reload=True` oder Neustart.
4. **`status.json` enthält keinen `device`-Schlüssel**, den
   `spotify/playback.py::_get_current_volume` zu lesen versucht — der Volume-Lesepfad fällt
   daher immer auf den Spotify-API-Call zurück. Die Volume-Funktionen
   (`set_volume`/`volume_up`/`volume_down`) haben zudem **keinen HTTP-Endpoint**.
5. **`history.track_id` ohne FK-Constraint** — bewusst denormalisiert; `PRAGMA
   foreign_keys=ON` ist daher derzeit wirkungslos.
6. **Pause/Resume erzeugt keine neue History-Zeile:** eine „Session" endet erst beim
   Track-*Wechsel*. Wer denselben Track nach Stunden erneut hört (ohne dass dazwischen ein
   anderer lief), verlängert die alte History-Zeile statt eine neue zu beginnen.
7. **Genre-Cache friert Genres ein:** Genres werden nur beim ersten Auftauchen eines Tracks
   von Spotify geholt; spätere Genre-Änderungen bei Spotify erreichen die DB nie (Trade-off
   zugunsten gesparter API-Calls).
8. **`api.lock` wird von der API nie selbst entfernt** (nur `spotify-db --stop` räumt es weg);
   nach einem Crash bleibt ein verwaistes Lock, das aber durch die PID-Liveness-Prüfung
   unschädlich ist.
9. **Fehlertexte in API-Antworten** geben `str(e)` ungefiltert zurück — hinter dem
   API-Key-Schutz akzeptiert, aber bei öffentlicher Exposition zu bedenken.
10. **`ruff.toml` ignoriert Regeln, die gar nicht selektiert sind** (`G004`, `S101`,
    `COM812`, `ISC001`, `TRY003`) — harmlos, aber redundant.
