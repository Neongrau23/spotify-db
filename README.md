# spotify-db

Spotify-Hörverlauf-Tracker. Ein Hintergrundprozess pollt den gerade laufenden Song über die
Spotify Web API und speichert Tracks, Wiedergabe-Verlauf und Hörzeit in einer lokalen
SQLite-Datenbank. Eine API-Key-geschützte REST-API (FastAPI) stellt die Daten im Heimnetz
bereit.

Gedacht für ein Gerät, das ohnehin durchläuft: einen Heimserver, einen Raspberry Pi oder ein
altes Android-Handy mit [Termux](https://termux.dev).

## Funktionen

- **Hörverlauf:** Neue Verlaufszeile bei jedem Track-Wechsel. Als Hörzeit zählt nur die
  tatsächlich verstrichene Zeit, nicht die Songlänge.
- **Genre- und Artist-Anreicherung:** Die Datenbank dient als Cache, deshalb fällt pro Track
  nur ein einziger zusätzlicher Spotify-Call an.
- **REST-API** mit Top-Listen, Statistiken, gefiltertem Verlauf und Live-Status. Swagger-UI
  unter `/docs`.
- **Wiedergabesteuerung:** Play/Pause, Weiter, Zurück und Spulen über die API.
- **Pro Aufrufer ein eigener API-Key**, jeder einzeln widerrufbar. Gespeichert wird nur der Hash.
- **Backups:** alle 30 Minuten lokal (die letzten 7 bleiben erhalten), beim Beenden optional
  zusätzlich per `scp` auf einen anderen Rechner.
- **Optional:** statischer Web-Server fürs Heimnetz und eine extern befüllte Enrichment-DB
  (`songs.db`, z. B. mit BPM, Tonart und Lyrics).

## Voraussetzungen

- Python ≥ 3.12 (unter Termux: `pkg install python`)
- Eine App im [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) für
  Client-ID, Client-Secret und Redirect-URI. Der Account, dem die App gehört, braucht ein
  aktives Premium-Abo, sonst lehnt Spotify die API-Anfragen ab.
- Optional: `ssh`/`scp` für die Remote-Kopie der Backups (unter Termux: `pkg install openssh`)

## Einrichtung

```sh
git clone https://github.com/Neongrau23/spotify-db.git
cd spotify-db
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

`pip install -e .` ist Pflicht: Erst die Installation legt das Kommando `spotify-db` an und
macht `python -m spotify_db.…` lauffähig.

**1. Spotify-Zugangsdaten eintragen.** `.env.example` nach `.env` kopieren und die Werte der
Spotify-App eintragen. Die Redirect-URI muss exakt so auch im Dashboard der App stehen.

```sh
cp .env.example .env
```

**2. Konfiguration anpassen (optional).** Ohne `config.json` gelten die eingebauten Defaults.
Die Datei ist nur nötig, wenn davon abgewichen werden soll, etwa um die Remote-Kopie der
Backups einzuschalten:

```sh
cp config.example.json config.json
```

Alle Schlüssel stehen in [docs/betrieb.md](docs/betrieb.md#schlüssel-in-configjson).

**3. Bei Spotify anmelden.** Den Tracker einmal im Vordergrund starten und die Zustimmung im
Browser bestätigen. Danach liegt der Token in `data/.spotify_cache` und erneuert sich von
selbst. Anschließend mit `Ctrl+C` beenden.

```sh
python -m spotify_db.spotify.tracker
```

**4. API-Key anlegen.** Solange kein Key existiert, antwortet die API mit `503`. Der Klartext
wird nur beim Anlegen einmal angezeigt.

```sh
python -m spotify_db.api.keys create --name handy
```

## Benutzung

```sh
spotify-db --run          # Tracker, API und Web-Server im Hintergrund starten
spotify-db --status       # Prozess-Status und aktueller Track
spotify-db --stop         # alles stoppen (wartet auf das finale Backup)
```

Mit `--tracker`, `--api` oder `--web` lassen sich die Prozesse einzeln starten und stoppen.
Alle Befehle stehen in [docs/architektur.md](docs/architektur.md#cli-befehle).

Auf Android (Termux) verhindert `termux-wake-lock`, dass die Prozesse bei ausgeschaltetem
Bildschirm schlafen gelegt werden. Weitere Plattform-Hinweise stehen in
[docs/betrieb.md](docs/betrieb.md#plattform-hinweise).

Die API lauscht auf Port `15001`:

```sh
curl -H "X-API-Key: <dein-key>" http://127.0.0.1:15001/live
```

Die Swagger-UI unter `http://127.0.0.1:15001/docs` ist ohne Key erreichbar. Über „Authorize"
lässt sich dort ein Key hinterlegen. Alle Endpunkte mit Beispielantworten stehen in der
[API-Referenz](docs/api.md).

Die API ist für das Heimnetz gedacht und nicht fürs Internet: kein TLS, kein Tunnel, kein
Reverse-Proxy.

## Dokumentation

Die ausführliche Doku liegt in [`docs/`](docs/index.md): Architektur und Datenfluss,
Tracker-Loop, Datenbankschema und Backups, API-Referenz, Konfiguration und Deployment sowie
bekannte Eigenheiten.

## Entwicklung

```sh
ruff check .
ruff format .
```

Es gibt keine Test-Suite. Geprüft wird manuell: Tracker im Vordergrund starten und die API mit
`curl` abfragen. Konventionen und bekannte Fallstricke stehen in
[docs/entwicklung.md](docs/entwicklung.md).

## Lizenz

[MIT](LICENSE)
