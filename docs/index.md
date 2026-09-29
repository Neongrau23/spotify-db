# spotify-db — Dokumentation

Lokaler Spotify-Tracker. Pollt den aktuell spielenden Song über die Spotify Web API,
speichert Tracks, Wiedergabe-Verlauf und Hörzeit in einer SQLite-Datenbank und stellt die
Daten über eine API-Key-geschützte FastAPI-REST-API bereit. Die Datenbank wird
regelmäßig lokal gesichert und optional per scp auf einen anderen Rechner kopiert.

Setup und Schnellstart stehen in der [README](../README.md).

## Themen

| Datei | Inhalt |
| --- | --- |
| [architektur.md](architektur.md) | Gesamtbild & Datenfluss, Prozessmanager `main.py`, Laufzeitdateien & Filesystem-IPC (`data/`), API-Interna (App-Setup, Gateway) |
| [tracker.md](tracker.md) | Tracker-Loop (Poll, Dead Reckoning, Hörzeit, `status.json`) und Spotify-Schicht (Auth, Collector, Playback) |
| [datenbank.md](datenbank.md) | SQLite-Verbindungsmodell, Schema aller Tabellen (inkl. `songs`), Lese-/Schreibfunktionen, Backup & Remote-Kopie |
| [api.md](api.md) | Endpunkt-Referenz mit Beispielantworten, Auth, Fehlerformat |
| [betrieb.md](betrieb.md) | Konfiguration (`config.json`, `.env`) und Deployment |
| [entwicklung.md](entwicklung.md) | Abhängigkeiten, Linting, Konventionen, bekannte Eigenheiten & Fallstricke |

## Pflege

- Jeder Fakt steht an **genau einer** Stelle; andere Dateien verlinken dorthin.
- Routen-Änderungen → `api.md` mitziehen (Pfade und Antwort-Shapes sind ein stabiler Vertrag).
- Keine Datei-Inventare pflegen — die Struktur zeigt `src/` selbst.
