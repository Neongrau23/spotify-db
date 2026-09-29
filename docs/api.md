# spotify-db API — Endpunkt-Referenz

Die API läuft als FastAPI/uvicorn-Server (ASGI) und ist **lokal und im Heimnetz** erreichbar:

- Lokal: `http://127.0.0.1:15001/<endpunkt>`
- Heimnetz: `http://<geräte-ip>:15001/<endpunkt>`

Die Routen werden direkt auf Root-Pfaden serviert.

**Interaktive Doku:** Unter `http://127.0.0.1:15001/docs` liegt die Swagger-UI, unter
`/openapi.json` das OpenAPI-Schema. Beide sind **ohne API-Key** erreichbar (LAN-only,
Single-User); alle Daten-Endpunkte bleiben key-geschützt. Über den **„Authorize"-Button**
in der Swagger-UI lässt sich der Key (als `X-API-Key` oder `Bearer`) hinterlegen, sodass
„Try it out"-Requests authentifiziert laufen.

---

## Authentifizierung

Jede Anfrage (außer `OPTIONS`) benötigt einen API-Key. Zwei Header-Varianten sind gültig:

| Header              | Beispiel                                    |
|---------------------|---------------------------------------------|
| `X-API-Key`         | `X-API-Key: mein-geheimer-key`              |
| `Authorization`     | `Authorization: Bearer mein-geheimer-key`   |

Die Keys liegen in der Datenbank (Tabelle `api_keys`) — **pro Aufrufer ein eigener,
einzeln widerrufbarer Key**. Gespeichert wird nur der sha256-Hash; der Klartext wird
genau einmal beim Erstellen angezeigt. Verwaltung ausschließlich lokal per CLI
(bewusst kein HTTP-Endpunkt):

```sh
python -m spotify_db.api.keys create --name handy    # Key erzeugen (Klartext einmalig)
python -m spotify_db.api.keys list                   # alle Keys + Status
python -m spotify_db.api.keys revoke --id 3          # widerrufen (greift sofort)
python -m spotify_db.api.keys revoke --name handy
```

Ein Widerruf greift sofort, weil das Gateway pro Request in der DB nachschlägt —
kein Neustart nötig.

**Fehlerantworten:**

| Situation                          | HTTP-Status | Body                                                                 |
|------------------------------------|-------------|----------------------------------------------------------------------|
| Keine aktiven Keys angelegt        | `503`       | `{"error": "Server misconfigured: keine aktiven API-Keys angelegt"}` |
| Key fehlt, falsch oder widerrufen  | `401`       | `{"error": "Unauthorized"}`                                          |

**CORS:** Starlettes `CORSMiddleware` setzt die `Access-Control-*`-Header (sie liegt außen,
also auch über den 401/503-Antworten). Erlaubte Origins steuert der Schlüssel `cors_origins`
in der `config.json` (Default `["*"]` — vertretbar, da jede Antwort key-geschützt ist; wird
beim Start gelesen). `OPTIONS`-Preflights sowie `/docs` und `/openapi.json` sind ohne Key erlaubt.

---

## Datenquellen — was kommt woher?

Die Endpunkte ziehen ihre Daten aus **fünf verschiedenen Quellen**. Diese Unterscheidung ist
der rote Faden dieser Doku: Sie zeigt, was wirklich aus der gespeicherten Datenbank stammt und
was zur Anfragezeit woandersher geholt wird.

- 🗄️ **Datenbank** — SQLite-Datei `spotify.db`, Tabellen `tracks`/`history`. Vom Tracker zuvor
  geschrieben; **kein** Spotify-Call zur Anfragezeit.
- 🎚️ **Songs-DB** — SQLite-Datei `songs.db`, Tabelle `songs`. **Extern befüllte** Enrichment-DB
  (BPM, Key, Camelot, Audio-Features, Lyrics, Credits, Links …), über denselben `track_id` mit
  `spotify.db` verknüpft. spotify-db liest hier nur (read-only) und schreibt sie nie.
- 📡 **Status-File** — `status.json`, vom Tracker sekündlich geschrieben (Live-Wiedergabezustand);
  **kein** Spotify-Call zur Anfragezeit.
- 🎵 **Spotify (live)** — direkter Spotify-API-Call (Playback-Steuerung).
- ⚙️ **Meta** — aus dem OpenAPI-Schema (`app.openapi()`) gebaut (Selbstbeschreibung); kein Datenzugriff.

> Wichtig: Auch die 🗄️-Datenbank-Endpunkte enthalten ursprünglich aus Spotify stammende Daten
> (Trackname, Genres, Cover …) — aber diese wurden **vorher vom Tracker** geholt und persistiert.
> Zur Anfragezeit fragt **kein** Datenbank- oder Status-Endpunkt Spotify an. Nur die 🎵-Endpunkte
> reden live mit Spotify.

---

## Alle Endpunkte auf einen Blick

### 🗄️ Datenbank

Lesen ausschließlich aus der lokalen SQLite-Datenbank (Tabellen `tracks`/`history`). Das sind
die Endpunkte mit den „echten", persistierten Daten.

| Methode | Pfad                         | Liest aus          | Beschreibung                                     |
|---------|------------------------------|--------------------|--------------------------------------------------|
| GET     | [`/tracks`](#get-tracks)                    | `tracks`           | Alle Tracks (mit optionalem Filter)              |
| GET     | [`/track/<track_id>`](#get-tracktrack_id)          | `tracks`           | Einzelner Track nach Spotify-ID                  |
| GET     | [`/history`](#get-history)                   | `history`          | Hör-Sessions (paginiert, filterbar)              |
| GET     | [`/top10/listen`](#get-top10listen)              | `tracks`           | Top-N nach Gesamthörzeit (`?n=`, Default 10)     |
| GET     | [`/top10/genres`](#get-top10genres)              | `tracks`           | Top-N Genres nach aggregierter Hörzeit (`?n=`)   |
| GET     | [`/top/artists`](#get-topartists)               | `tracks`           | Top-N Künstler nach aggregierter Hörzeit (`?n=`) |
| GET     | [`/top/plays`](#get-topplays)                 | `history`+`tracks` | Top-N Tracks nach Wiedergabe-Anzahl (`?n=`)      |
| GET     | [`/stats`](#get-stats)                     | `tracks`+`history` | Gesamt-Statistik der Datenbank                   |
| GET     | [`/tracks/<track_id>/stats`](#get-trackstrack_idstats)   | `tracks`           | Hörzeit + Rang für einen Track                   |
| GET     | [`/tracks/<track_id>/details`](#get-trackstrack_iddetails) | `tracks`           | Gecachte Artist-/Genre-Details                   |

### 🎚️ Songs-DB

Liest aus der extern befüllten Enrichment-DB `songs.db` (Tabelle `songs`). Read-only; ist die
Datei nicht vorhanden, antworten diese Endpunkte mit `503`.

| Methode | Pfad                | Liest aus            | Beschreibung                                          |
|---------|---------------------|----------------------|-------------------------------------------------------|
| GET     | [`/songs`](#get-songs)            | `songs`              | Alle Songs (paginiert, durchsuchbar, mit Feld-Auswahl)|
| GET     | [`/songs/current`](#get-songscurrent)    | `songs`+`status.json`| Alle Enrichment-Infos zum **aktuell laufenden** Track |
| GET     | [`/songs/<track_id>`](#get-songstrack_id) | `songs`              | Alle Enrichment-Infos zu einem Track                  |

### 📡 Status-File

Liest den vom Tracker sekündlich geschriebenen Live-Wiedergabezustand. Kein Spotify-Call zur
Anfragezeit.

| Methode | Pfad | Quelle                               | Beschreibung                  |
|---------|---------------|-----------------------------|-------------------------------|
| GET     | [`/live`](#get-live) | `status.json` (+ DB-Anreicherung) | Aktueller Tracker-Live-Status |

> `/live` ist primär ein Status-File-Endpunkt, ergänzt aber **drei Felder aus der DB**
> (`added_at`, `db_listen_ms`, `db_listen_time`) — siehe Detailbeschreibung unten.

### 🎵 Spotify-Proxy

Leiten Befehle direkt an die Spotify-API weiter. Jeder dieser Endpunkte setzt nach dem
Spotify-Call ein **Resync-Flag**, das den Tracker zu einem sofortigen Re-Poll veranlasst
(innerhalb von 100 ms).

| Methode | Pfad                | Beschreibung             |
|---------|---------------------|--------------------------|
| POST    | [`/control/toggle`](#post-controltoggle)   | Play/Pause toggeln       |
| POST    | [`/control/next`](#post-controlnext)     | Nächster Track           |
| POST    | [`/control/previous`](#post-controlprevious) | Vorheriger Track         |
| POST    | [`/control/seek`](#post-controlseek)     | Position im Track setzen |

### ⚙️ Meta

| Methode | Pfad | Beschreibung                |
|---------|---------------|--------------------|
| GET     | [`/`](#get-)           | Endpunkt-Discovery |

---

## 🗄️ Datenbank-Endpunkte (`spotify.db`)

Alle Endpunkte in diesem Abschnitt lesen **ausschließlich** aus der lokalen SQLite-Datenbank.
Es findet zur Anfragezeit kein Spotify-API-Call statt — die Daten hat der Tracker zuvor
geschrieben. Das zugrunde liegende Schema steht in [datenbank.md](datenbank.md#schema).

### Track-Daten

#### `GET /tracks`

Gibt alle Tracks aus der Datenbank zurück. Unterstützt optionale Filterung und Feldauswahl.

**Query-Parameter:**

| Parameter | Beschreibung                                                                        | Beispiel                      |
|-----------|-------------------------------------------------------------------------------------|------------------------------ |
| `genre`   | Komma-getrennte Genres; ein Track muss **alle** enthalten (AND-Verknüpfung)         | `?genre=rock,pop`             |
| `fields`  | Komma-getrennte Liste der gewünschten Felder (unbekannte Felder werden ignoriert)   | `?fields=track_id,track_name` |

**Erlaubte Felder für `fields`:**
`track_id`, `track_name`, `artists`, `album`, `genres`, `duration_ms`, `release_date`,
`added_at`, `last_seen`, `total_listen_ms`, `album_cover_url`, `artist_images`, `spotify_url`

**Antwort:**
```json
{
  "data": [
    {
      "track_id": "4u7EnebtmKWzUH433cf5Qv",
      "track_name": "Bohemian Rhapsody",
      "artists": "Queen",
      "album": "A Night at the Opera",
      "genres": "classic rock, rock",
      "duration_ms": 354947,
      "release_date": "1975-11-21",
      "added_at": "2026-06-03 14:22:11",
      "last_seen": "2026-06-10 09:15:03",
      "total_listen_ms": 9180000,
      "album_cover_url": "https://i.scdn.co/image/...",
      "artist_images": "https://i.scdn.co/image/...",
      "spotify_url": "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
    }
  ],
  "count": 1
}
```

> **Hinweis:** `artists` und `genres` sind komma-getrennte Strings, keine Arrays — im Gegensatz
> zu `/live`.

---

#### `GET /track/<track_id>`

Gibt vollständige Daten für einen einzelnen Track nach seiner Spotify-ID zurück.

**Pfad-Parameter:** `track_id` — die Spotify-Track-ID (z.B. `4u7EnebtmKWzUH433cf5Qv`)

**Antwort:** wie ein Eintrag aus `/tracks`, eingebettet in `{"data": {...}}`.

**Fehler:** `404` wenn der Track nicht in der DB ist.

---

#### `GET /history`

Gibt Hör-Sessions zurück, paginiert und nach Track-ID oder Zeitraum filterbar.

Eine neue History-Zeile entsteht, wenn der Tracker erkennt, dass ein neuer Track begonnen hat.
Die `total_listen_ms` in History ist die Hörzeit **dieser einen Session** (nicht kumuliert).

**Query-Parameter:**

| Parameter  | Beschreibung                                | Beispiel              | Default |
|------------|---------------------------------------------|-----------------------|---------|
| `limit`    | Maximale Anzahl Ergebnisse (1–1000)         | `?limit=50`           | `100`   |
| `offset`   | Überspringt N Einträge (für Pagination)     | `?offset=100`         | `0`     |
| `track_id` | Filtert auf einen bestimmten Track          | `?track_id=4u7Ene...` | –       |
| `from`     | Frühestes Datum inklusiv (`YYYY-MM-DD`)     | `?from=2026-06-01`    | –       |
| `to`       | Spätestes Datum inklusiv (`YYYY-MM-DD`)     | `?to=2026-06-11`      | –       |

**Antwort:**
```json
{
  "data": [
    {
      "id": 512,
      "track_id": "4u7EnebtmKWzUH433cf5Qv",
      "track_name": "Bohemian Rhapsody",
      "artists": "Queen",
      "album": "A Night at the Opera",
      "total_listen_ms": 354947,
      "played_at": "2026-06-10 09:15:03"
    }
  ],
  "count": 50,
  "total": 1234,
  "offset": 0
}
```

| Feld     | Beschreibung                                              |
|----------|-----------------------------------------------------------|
| `count`  | Anzahl Einträge in dieser Antwort                         |
| `total`  | Gesamtanzahl matching Einträge (für Pagination nützlich)  |
| `offset` | Gesendeter Offset-Wert (Echo)                             |

---

### Top-Listen

#### `GET /top10/listen`

Die meistgehörten Tracks, sortiert nach kumulierter Gesamthörzeit (`total_listen_ms` aus
der `tracks`-Tabelle). Gibt alle Track-Felder zurück — gleiche Struktur wie `/tracks`.

**Query-Parameter:** `?n=` — Anzahl der Einträge (1–100, Default: 10)

---

#### `GET /top10/genres`

Die Genres mit der höchsten aggregierten Hörzeit. Jeder Track wird anteilig auf all seine
Genres aufgeteilt (nicht gewichtet — ein Track mit 3 Genres zählt für alle 3 voll).

Genres mit dem Wert `"Keine Genres gefunden"` und Tracks ohne Hörzeit werden ignoriert.

**Query-Parameter:** `?n=` — Anzahl der Einträge (1–100, Default: 10)

**Antwort:**
```json
{
  "data": [
    {
      "genre": "classic rock",
      "total_listen_ms": 18360000,
      "total_listen_time": "05:06:00"
    }
  ],
  "count": 10
}
```

---

#### `GET /top/artists`

Die Künstler mit der höchsten aggregierten Hörzeit. Tracks mit mehreren Künstlern zählen
für jeden Künstler voll (gleiche Logik wie bei Genres).

**Query-Parameter:** `?n=` — Anzahl der Einträge (1–100, Default: 10)

**Antwort:**
```json
{
  "data": [
    {
      "artist": "Queen",
      "total_listen_ms": 18360000,
      "total_listen_time": "05:06:00"
    }
  ],
  "count": 10
}
```

---

#### `GET /top/plays`

Die meistgespielten Tracks nach Anzahl der History-Einträge. Kurze Songs, die häufig
wiederholt werden, tauchen hier auf — im Gegensatz zu `top10/listen`, das nur Hörzeit zählt.

**Query-Parameter:** `?n=` — Anzahl der Einträge (1–100, Default: 10)

**Antwort:**
```json
{
  "data": [
    {
      "track_id": "4u7EnebtmKWzUH433cf5Qv",
      "track_name": "Bohemian Rhapsody",
      "artists": "Queen",
      "album": "A Night at the Opera",
      "play_count": 47,
      "total_listen_ms": 9180000,
      "album_cover_url": "https://i.scdn.co/image/...",
      "spotify_url": "https://open.spotify.com/track/4u7EnebtmKWzUH433cf5Qv"
    }
  ],
  "count": 10
}
```

---

### Statistik

#### `GET /stats`

Gesamtzahlen über die gesamte Datenbank.

**Antwort:**
```json
{
  "total_tracks": 1234,
  "total_listen_ms": 123456789,
  "total_listen_time": "01d 10h 17m",
  "total_plays": 5678,
  "unique_artists": 234,
  "unique_albums": 456,
  "unique_genres": 89,
  "avg_listen_ms_per_track": 100045,
  "first_seen": "01.01.2026",
  "last_seen": "11.06.2026"
}
```

| Feld                      | Beschreibung                                                  |
|---------------------------|---------------------------------------------------------------|
| `total_tracks`            | Anzahl aller Tracks in der DB                                 |
| `total_listen_ms`         | Kumulierte Gesamthörzeit in ms                                |
| `total_listen_time`       | Gesamthörzeit als `TTd HHh MMm`                               |
| `total_plays`             | Anzahl aller History-Einträge (Wiedergaben)                   |
| `unique_artists`          | Anzahl verschiedener Künstler (aus komma-getrennten Strings)  |
| `unique_albums`           | Anzahl verschiedener Alben                                    |
| `unique_genres`           | Anzahl verschiedener Genres                                   |
| `avg_listen_ms_per_track` | Durchschnittliche Hörzeit pro Track in ms                     |
| `first_seen`              | Datum des ältesten Tracks in der DB (`DD.MM.YYYY`)            |
| `last_seen`               | Datum des zuletzt gesehenen Tracks (`DD.MM.YYYY`)             |

---

#### `GET /tracks/<track_id>/stats`

Hörzeit und Rang für einen einzelnen Track. Dieser Endpunkt ist der Hot-Path für das Live-Widget.

**Antwort:**
```json
{
  "total_listen_ms": 9180000,
  "rank": 7
}
```

Der `rank` gibt an, als wie-vielthöchster Track dieser Track nach Hörzeit dasteht (1 = Platz 1).
Bei unbekannter Track-ID werden `0` für beide Werte zurückgegeben (kein `404`).

---

#### `GET /tracks/<track_id>/details`

Gecachte Artist- und Genre-Details für einen Track. Gibt `404` zurück, wenn der Track
unbekannt ist oder noch keine Genre-Daten vorhanden sind.

Diese Daten werden beim ersten Auftauchen eines Tracks vom Tracker per Spotify Artist-API
geholt und dann lokal gecacht — folgepolls brauchen keinen weiteren API-Call. Der Abruf hier
liest also nur den Cache aus der DB, **nicht** Spotify.

**Antwort:**
```json
{
  "artists": ["Queen"],
  "genres": ["classic rock", "rock"],
  "artist_images": "https://i.scdn.co/image/..., https://i.scdn.co/image/..."
}
```

> **Hinweis:** `artists` und `genres` sind hier Listen (aus den komma-getrennten Spalten
> aufgesplittet), `artist_images` bleibt dagegen ein komma-getrennter String — genau so, wie
> er in der Spalte `tracks.artist_images` steht.

---

## 🎚️ Songs-DB-Endpunkte (`songs.db`)

Diese Endpunkte lesen aus der **extern befüllten** Enrichment-DB `songs.db` (Tabelle `songs`),
über denselben `track_id` mit `spotify.db` verknüpft. spotify-db schreibt sie nie — der Zugriff
ist read-only. Ist die Datei nicht vorhanden, antworten beide Endpunkte mit `503`
(`{"error": "songs.db nicht gefunden — ...", ...}`).

Die zurückgegebenen Felder entsprechen dem [Schema in datenbank.md](datenbank.md#schema) und werden zur
Laufzeit aus der Tabelle ermittelt — kommen extern neue Spalten dazu, tauchen sie automatisch auf.

### `GET /songs`

Liefert alle Songs der Enrichment-DB, paginiert und durchsuchbar. Sortiert nach `saved_at`
absteigend (zuletzt gespeicherte zuerst).

**Query-Parameter:**

| Parameter | Beschreibung                                                 | Beispiel            | Default  |
|-----------|--------------------------------------------------------------|---------------------|----------|
| `limit`   | Maximale Anzahl Ergebnisse (1–1000)                          | `?limit=50`         | `100`    |
| `offset`  | Überspringt N Einträge (für Pagination)                      | `?offset=100`       | `0`      |
| `q`       | Suchstring (`LIKE` auf `title`, `artists`, `album`)          | `?q=Iglesias`       | –        |
| `fields`  | Komma-getrennte Spaltenauswahl (unbekannte werden ignoriert) | `?fields=title,bpm` | – (alle) |

**Antwort:** Struktur wie `/history` — `data`/`count`/`total`/`offset`. Jeder Eintrag enthält
alle Spalten der `songs`-Tabelle (oder die per `fields` gewählten).

```json
{
  "data": [ { "track_id": "2mBUuRrFCYWMtABGje0wVt", "title": "I'm So Bad - Iglesias Remix", "...": "..." } ],
  "count": 100,
  "total": 6297,
  "offset": 0
}
```

---

### `GET /songs/current`

Gibt **alle** Enrichment-Infos zum **aktuell laufenden** Track zurück — die `track_id` wird
automatisch aus `status.json` gelesen (kein Pfad-Parameter nötig). Praktisch für Widgets, die
DJ-Daten (BPM/Key/Camelot) zum gerade gespielten Song anzeigen, ohne die ID zu kennen.

**Antwort:** identische Struktur wie [`GET /songs/<track_id>`](#get-songstrack_id) (Feld `data`
mit allen Spalten der `songs`-Tabelle).

**Fehler:**

- `404` — Tracker läuft nicht / keine `status.json`, kein aktueller Track, **oder** der
  aktuelle Track ist (noch) nicht in `songs.db` enthalten.
- `503` — `songs.db` nicht vorhanden.

---

### `GET /songs/<track_id>`

Gibt **alle** Enrichment-Infos zu einem Track zurück (BPM, Key, Camelot, Audio-Features,
Lyrics, Credits, Links …). `404`, wenn die Track-ID nicht in `songs.db` steht.

**Pfad-Parameter:** `track_id` — die Spotify-Track-ID

**Antwort:**
```json
{
  "data": {
    "track_id": "2mBUuRrFCYWMtABGje0wVt",
    "title": "I'm So Bad - Iglesias Remix",
    "artists": "[\"Melanie Ribbe\", \"Chris Di Perri\", \"Iglesias\"]",
    "album": "Single",
    "release_date": "2023-07-28",
    "isrc": "ES94G2329005",
    "explicit": 0,
    "label": "elrow Music",
    "genres": "[\"Dance\", \"Electronic\", \"Funk\", \"House\", \"R&B/Soul\"]",
    "bpm": 127.0,
    "key": "G major",
    "key_pitch_class": 7,
    "key_mode": 1,
    "camelot": "9B",
    "duration_ms": 213000,
    "popularity": 31,
    "acousticness": 0.01,
    "danceability": 0.81,
    "energy": 0.87,
    "instrumentalness": 0.47,
    "liveness": 0.14,
    "speechiness": 0.04,
    "happiness": 0.68,
    "loudness": -9.0,
    "analysis": "I'm So Bad - Iglesias Remix is a upbeat ...",
    "lyrics": "[{\"section\": \"[Info]\", \"lines\": [...]}]",
    "synced_lyrics": null,
    "album_tracklist": "[]",
    "credits": "{}",
    "link_genius": null,
    "link_spotify": "https://open.spotify.com/track/2mBUuRrFCYWMtABGje0wVt",
    "link_tunebat": "https://tunebat.com/Info/...",
    "link_songstats": "https://songstats.com/track/...",
    "link_songbpm": "https://songbpm.com/@...",
    "saved_at": "2026-05-22T02:33:47.994314+00:00"
  }
}
```

> **Hinweis:** Zahlen kommen typisiert (`bpm`/Audio-Features als Float, `duration_ms`/
> `popularity` als Integer). `artists` und `genres` sind **JSON-Arrays als String** — anders als
> die komma-getrennten Strings in `spotify.db`. Felder wie `lyrics`, `analysis`,
> `album_tracklist`, `credits` enthalten JSON-Strings bzw. längere Texte. Welche Spalten
> existieren, bestimmt die extern befüllte DB (z. B. fehlt `synced_lyrics` in älteren Ständen).

---

## 📡 Status-File-Endpunkt (`status.json`)

### `GET /live`

Liest die vom Tracker sekündlich geschriebene `status.json` und gibt den aktuellen
Wiedergabe-Zustand zurück. Kein Spotify-API-Call — rein lokal. Falls der Tracker nicht
läuft und keine `status.json` existiert, gibt es `404`.

Der `progress_ms`-Wert wird vom Tracker per **Dead Reckoning** sekündlich interpoliert
(zwischen echten Spotify-Polls), ist also immer aktuell — auch ohne neuen Poll.

> **Quelle gemischt:** Die meisten Felder stammen aus `status.json`. **Drei** Felder reichert der
> Endpunkt zur Anfragezeit aus der `tracks`-Tabelle an: `added_at` (per `track_id`-Lookup) sowie
> `db_listen_ms` / `db_listen_time` (aus der Summe über alle Tracks). Diese sind im Feld-Überblick
> unten mit 🗄️ markiert. `play_count` wird inline aus `total_listen_ms / duration_ms` berechnet.

**Antwort:**
```json
{
  "username": "Max",
  "profile_image_url": "https://i.scdn.co/image/ab67...",
  "is_new": false,
  "session_total": 42,
  "session_unique": 31,
  "db_total": 1234,
  "song": "Bohemian Rhapsody",
  "artists": ["Queen"],
  "genres": ["classic rock", "rock"],
  "album_cover_url": "https://i.scdn.co/image/...",
  "duration": "05:55",
  "duration_ms": 354947,
  "progress": "01:23",
  "progress_ms": 83000,
  "is_playing": true,
  "total_listen_ms": 9180000,
  "total_listen_time": "02:33:00",
  "play_count": 25.8,
  "listen_rank": 7,
  "added_at": "03.06.2026",
  "db_listen_ms": 18360000,
  "db_listen_time": "00d 05h 06m",
  "api_error": null
}
```

> **`api_error` prüfen, bevor „nichts läuft" angezeigt wird.** Lehnt Spotify die Polls des
> Trackers ab (z.B. `403`, weil der App-Owner kein aktives Premium-Abo hat), liefert `/live`
> weiterhin `200` — aber mit leeren Track-Feldern und gesetztem `api_error`. Ohne diese Prüfung
> ist der Fehlerzustand von „gerade läuft einfach nichts" nicht zu unterscheiden.
>
> ```json
> "api_error": {
>   "status": 403,
>   "message": "Active premium subscription required for the owner of the app. …"
> }
> ```

**Felder:** (🗄️ = aus der DB angereichert, sonst aus `status.json`)

| Feld                | Typ     | Beschreibung                                                                  |
|---------------------|---------|-------------------------------------------------------------------------------|
| `username`          | string  | Spotify-Anzeigename des Nutzers (einmalig beim Tracker-Start geholt)          |
| `profile_image_url` | string  | URL zum Spotify-Profilbild (leer, wenn keins gesetzt)                         |
| `is_new`            | boolean | `true` wenn der Track in dieser Session das erste Mal in der DB erscheint     |
| `session_total`     | integer | Anzahl Plays in der laufenden Tracker-Session (inklusive Wiederholungen)      |
| `session_unique`    | integer | Anzahl verschiedener Tracks in dieser Session                                 |
| `db_total`          | integer | Gesamtzahl aller Tracks in der Datenbank                                      |
| `song`              | string  | Trackname                                                                     |
| `artists`           | array   | Liste der Künstlernamen                                                       |
| `genres`            | array   | Liste der Genres (von Spotify über Artist-API)                                |
| `album_cover_url`   | string  | URL zum Albumcover (Spotify CDN)                                              |
| `duration`          | string  | Gesamtdauer im Format `MM:SS`                                                 |
| `duration_ms`       | integer | Gesamtdauer in Millisekunden                                                  |
| `progress`          | string  | Aktuelle Position im Format `MM:SS`                                           |
| `progress_ms`       | integer | Aktuelle Position in Millisekunden (interpoliert, sekündlich aktualisiert)    |
| `is_playing`        | boolean | Gibt an ob gerade abgespielt wird                                             |
| `total_listen_ms`   | integer | Kumulierte Hörzeit dieses Tracks (aus DB) in ms                               |
| `total_listen_time` | string  | Kumulierte Hörzeit dieses Tracks als `HH:MM:SS` oder `MM:SS`                  |
| `play_count`        | float   | Wie oft der Track gehört wurde (`total_listen_ms / duration_ms`, Schritt 0,1) |
| `listen_rank`       | integer | Rang des Tracks nach Hörzeit (1 = meistgehört); 0 wenn unbekannt              |
| 🗄️ `added_at`        | string  | Datum, wann der Track erstmals in der DB gespeichert wurde (`DD.MM.YYYY`)    |
| 🗄️ `db_listen_ms`    | integer | Gesamte Hörzeit über alle Tracks in der DB in ms                             |
| 🗄️ `db_listen_time`  | string  | Gesamte Hörzeit über alle Tracks als `TTd HHh MMm`                           |
| `api_error`         | object  | `{status, message}` wenn Spotify den letzten Poll ablehnte, sonst `null`     |

---

## 🎵 Spotify-Proxy-Endpunkte (live)

Diese Endpunkte senden Befehle **direkt an die Spotify-API** und setzen danach ein
**Resync-Flag**, das den Tracker zu einem sofortigen Re-Poll veranlasst (innerhalb
von 100 ms). So ist der Live-Status nach einer Aktion sofort aktuell. Sie schreiben
nichts in die Datenbank.

### `POST /control/toggle`

Toggelt zwischen Play und Pause. Liest den aktuellen Zustand aus `status.json`, um einen
Spotify-API-Call zu sparen.

**Kein Request-Body nötig.**

**Antwort:**
```json
{"ok": true}
```

---

### `POST /control/next`

Springt zum nächsten Track in der aktuellen Wiedergabe-Queue.

**Kein Request-Body nötig.**

**Antwort:**
```json
{"ok": true}
```

---

### `POST /control/previous`

Springt zum vorherigen Track. Spotify-Standardverhalten: Ist die Position > ~3 s, springt
es an den Anfang des aktuellen Tracks statt zum vorherigen.

**Kein Request-Body nötig.**

**Antwort:**
```json
{"ok": true}
```

---

### `POST /control/seek`

Springt an eine bestimmte Position im aktuellen Track.

**Request-Body (JSON):**
```json
{
  "position_ms": 60000
}
```

| Feld          | Typ     | Pflicht | Beschreibung                        |
|---------------|---------|---------|-------------------------------------|
| `position_ms` | integer | ja      | Zielposition in Millisekunden (≥ 0) |

**Fehler:** `400` wenn `position_ms` fehlt oder kein Integer ist.

**Antwort:**
```json
{"ok": true}
```

---

## ⚙️ Meta-Endpunkt

### `GET /`

Gibt eine Liste aller registrierten Endpunkte zurück — gebaut aus dem OpenAPI-Schema
(`app.openapi()`). Nützlich, um zu prüfen welche Routen aktiv sind. Kein DB- oder Spotify-Zugriff.
Pfad-Parameter erscheinen als `{track_id}` (OpenAPI-Notation).

**Antwort:**
```json
{
  "count": 19,
  "endpoints": [
    {
      "path": "/live",
      "methods": ["GET"],
      "description": "Aktueller Tracker-Live-Status aus der vom Tracker geschriebenen Status-JSON."
    }
  ]
}
```

---

## Fehlerformat

Alle Endpunkte geben im Fehlerfall ein einheitliches JSON zurück:

```json
{"error": "Fehlermeldung als String"}
```

Endpunkte, die Listen zurückgeben (`/tracks`, `/history`, etc.), liefern bei
einem Serverfehler zusätzlich leere Arrays:

```json
{"error": "...", "data": [], "count": 0}
```
