"""Lese-Logik der Datenbank (Queries + Format-Helfer).

Alle Funktionen sind reine Reads über `database.read_lock()` — Schreibzugriffe
leben in `database.py`. Die API-Routen (`spotify_db.api.routes`) bleiben dünn und
rufen ausschließlich hierher; die Antwort-Shapes der Endpoints werden also von
diesen Funktionen bestimmt. Die Auswertungs-Befehle der CLI (`spotify_db.cli.stats`)
nutzen dieselben Funktionen, damit CLI und API dieselben Zahlen zeigen.
"""

from spotify_db.db.database import read_lock

# CONFIG: Spalten, die über den ?fields=-Parameter von /tracks abfragbar sind
ALLOWED_TRACK_COLUMNS = (
    "track_id",
    "track_name",
    "artists",
    "album",
    "genres",
    "duration_ms",
    "release_date",
    "added_at",
    "last_seen",
    "total_listen_ms",
    "album_cover_url",
    "artist_images",
    "spotify_url",
)

# CONFIG: Platzhalter-Genre, das bei Aggregationen ignoriert wird
_GENRE_PLACEHOLDER = "Keine Genres gefunden"


# SECTION: - Format-Helfer -


# DEF: Datum formatieren
def format_added_at(raw) -> str:
    """Wandelt einen DB-Zeitstempel ('YYYY-MM-DD HH:MM:SS') in 'DD.MM.YYYY' um.

    Gibt einen leeren String zurück, wenn der Wert fehlt oder unerwartet aussieht.
    """
    if not raw:
        return ""
    parts = str(raw)[:10].split("-")
    if len(parts) != 3 or not all(parts):
        return ""
    year, month, day = parts
    return f"{day}.{month}.{year}"


# DEF: Hörzeit formatieren (Tage.Stunden.Minuten)
def format_dd_hh_mm(total_ms: int) -> str:
    """Wandelt eine Dauer in Millisekunden in den String 'TTd HHh MMm' (Tage/Stunden/Minuten)."""
    total_minutes = (max(total_ms, 0) // 1000) // 60
    days = total_minutes // (60 * 24)
    hours = (total_minutes % (60 * 24)) // 60
    minutes = total_minutes % 60
    return f"{days:02}d {hours:02}h {minutes:02}m"


# DEF: Hörzeit formatieren (HH:MM:SS oder MM:SS)
def format_hh_mm_ss(total_ms: int) -> str:
    """Wandelt ms in 'HH:MM:SS' (wenn ≥ 1 Stunde) oder 'MM:SS'."""
    total_seconds = max(total_ms, 0) // 1000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


# SECTION: - Aggregations-Helfer -


# DEF: Komma-Liste (artists/genres) aufsplitten
def _split_names(raw, ignore=()) -> list[str]:
    """Zerlegt einen komma-getrennten DB-String in bereinigte Einzelnamen.

    Zentral, damit Gesamt- und Zeitraum-Auswertungen dieselben Regeln anwenden. Namen
    mit Komma ("Tyler, The Creator") werden dabei zerlegt — bekannte Eigenheit, siehe
    docs/entwicklung.md.

    Args:
        raw: Spaltenwert, z.B. "Daft Punk, The Weeknd"; leer/None ergibt [].
        ignore: Namen, die wegfallen (z.B. der Genre-Platzhalter).

    Returns:
        list[str]: Die nicht-leeren, getrimmten Namen.
    """
    if not raw:
        return []
    names = (part.strip() for part in raw.split(","))
    return [name for name in names if name and name not in ignore]


# DEF: Hörzeit pro Einzelname aufsummieren (Top-Artists/-Genres)
def _top_by_name(rows, column, key, n, ignore=()):
    """Summiert `total_listen_ms` pro Einzelname einer Komma-Spalte und liefert die Top N.

    Args:
        rows: Zeilen mit `column` und `total_listen_ms`.
        column: Name der Komma-Spalte ("artists" oder "genres").
        key: Schlüssel des Namens im Ergebnis ("artist" oder "genre").
        n: Anzahl der Einträge.
        ignore: Namen, die nicht mitgezählt werden.

    Returns:
        list[dict]: `{key, total_listen_ms, total_listen_time}`, absteigend nach Hörzeit.
    """
    totals: dict[str, int] = {}
    for row in rows:
        listen_ms = row["total_listen_ms"] or 0
        if listen_ms == 0:
            continue
        for name in _split_names(row[column], ignore):
            totals[name] = totals.get(name, 0) + listen_ms

    top = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:n]
    return [
        {key: name, "total_listen_ms": ms, "total_listen_time": format_hh_mm_ss(ms)}
        for name, ms in top
    ]


# SECTION: - Hot-Path-Helfer (Live-Widget) -


# DEF: Kombinierte Track-Stats (Hörzeit + Rang)
def get_track_stats(track_id):
    """Liefert {total_listen_ms, rank} für einen Track. 0/0 wenn unbekannt."""
    with read_lock() as conn:
        row = conn.execute(
            "SELECT total_listen_ms FROM tracks WHERE track_id = ?", (track_id,)
        ).fetchone()
        if not row:
            return {"total_listen_ms": 0, "rank": 0}

        track_time = row["total_listen_ms"] or 0
        rank_row = conn.execute(
            "SELECT COUNT(*) FROM tracks WHERE total_listen_ms > ?", (track_time,)
        ).fetchone()
        return {
            "total_listen_ms": track_time,
            "rank": (rank_row[0] + 1) if rank_row else 1,
        }


# DEF: Track-Details aus dem Cache (für spotify_db.spotify.collector)
def get_track_details_or_none(track_id):
    """Liefert gespeicherte Artist-/Genre-Details, oder None wenn unbekannt / ohne Genres."""
    with read_lock() as conn:
        row = conn.execute(
            "SELECT artists, genres, artist_images FROM tracks WHERE track_id = ?",
            (track_id,),
        ).fetchone()

    if not row or not row["genres"]:
        return None

    return {
        "artists": [a.strip() for a in row["artists"].split(",")] if row["artists"] else [],
        "genres": [g.strip() for g in row["genres"].split(",")] if row["genres"] else [],
        "artist_images": row["artist_images"] or "",
    }


# DEF: Anzahl aller Tracks
def get_total_tracks_count():
    """Gibt die Gesamtanzahl der gespeicherten Tracks zurück."""
    with read_lock() as conn:
        row = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
        return row[0] if row else 0


# DEF: Summierte Hörzeit über alle Tracks
def get_total_listen_ms():
    """Gibt die über alle Tracks aufsummierte Hörzeit in Millisekunden zurück."""
    with read_lock() as conn:
        row = conn.execute("SELECT SUM(total_listen_ms) FROM tracks").fetchone()
        return (row[0] or 0) if row else 0


# DEF: Erstes "gehört am" eines Tracks (für /live)
def get_track_added_at(track_id) -> str:
    """Liefert das added_at eines Tracks als 'DD.MM.YYYY', oder '' wenn unbekannt."""
    with read_lock() as conn:
        row = conn.execute("SELECT added_at FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
    return format_added_at(row["added_at"]) if row else ""


# SECTION: - Listen-Queries (Endpoints) -


# DEF: Tracks auflisten (mit Genre-Filter und Feld-Auswahl)
def list_tracks(genres=None, fields=None):
    """Liefert Tracks als Dict-Liste, optional gefiltert nach Genres / mit Spalten-Auswahl.

    Args:
        genres: Liste von Genre-Substrings; mehrere wirken als UND-Filter.
        fields: Gewünschte Spalten; unbekannte werden ignoriert (Allow-List).

    Returns:
        list[dict]: Die passenden Tracks.
    """
    columns = "*"
    if fields:
        requested = [f for f in fields if f in ALLOWED_TRACK_COLUMNS]
        if requested:
            columns = ", ".join(requested)

    if genres:
        query = f"SELECT {columns} FROM tracks WHERE " + " AND ".join(
            ["genres LIKE ?"] * len(genres)
        )
        params = [f"%{g}%" for g in genres]
        with read_lock() as conn:
            rows = conn.execute(query, params).fetchall()
    else:
        with read_lock() as conn:
            rows = conn.execute(f"SELECT {columns} FROM tracks").fetchall()
    return [dict(row) for row in rows]


# DEF: Einzelnen Track holen
def get_track(track_id):
    """Liefert alle Spalten eines Tracks als Dict, oder None wenn unbekannt."""
    with read_lock() as conn:
        row = conn.execute("SELECT * FROM tracks WHERE track_id = ?", (track_id,)).fetchone()
    return dict(row) if row else None


# DEF: History auflisten (paginiert + filterbar)
def list_history(limit, offset, track_id=None, from_date=None, to_date=None):
    """Liefert Hör-Sessions absteigend nach Zeitpunkt, paginiert und filterbar.

    Args:
        limit: Maximale Anzahl Einträge.
        offset: Überspringt die ersten N Einträge.
        track_id: Optionaler Filter auf einen Track.
        from_date: Untergrenze als 'YYYY-MM-DD' (inklusiv).
        to_date: Obergrenze als 'YYYY-MM-DD' (inklusiv, ganzer Tag).

    Returns:
        tuple[list[dict], int]: (Einträge dieser Seite, Gesamtanzahl Treffer).
    """
    conditions: list[str] = []
    params: list = []
    if track_id:
        conditions.append("track_id = ?")
        params.append(track_id)
    if from_date:
        conditions.append("played_at >= ?")
        params.append(from_date)
    if to_date:
        # +1 Tag damit der gesamte to_date-Tag inkludiert wird
        conditions.append("played_at < date(?, '+1 day')")
        params.append(to_date)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with read_lock() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM history {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM history {where} ORDER BY played_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return [dict(row) for row in rows], total


# SECTION: - Top-N-Queries -


# DEF: Top-N Tracks nach Hörzeit
def top_tracks_by_listen(n):
    """Liefert die N Tracks mit der höchsten Gesamthörzeit."""
    with read_lock() as conn:
        rows = conn.execute(
            "SELECT * FROM tracks ORDER BY total_listen_ms DESC LIMIT ?", (n,)
        ).fetchall()
    return [dict(row) for row in rows]


# DEF: Top-N Genres nach Hörzeit
def top_genres_by_listen(n):
    """Aggregiert die Hörzeit pro Genre (kommagetrennte Spalte) und liefert die Top N."""
    with read_lock() as conn:
        rows = conn.execute(
            "SELECT genres, total_listen_ms FROM tracks WHERE genres IS NOT NULL AND genres != ''"
        ).fetchall()
    return _top_by_name(rows, "genres", "genre", n, ignore=(_GENRE_PLACEHOLDER,))


# DEF: Top-N Artists nach Hörzeit
def top_artists_by_listen(n):
    """Aggregiert die Hörzeit pro Künstler (kommagetrennte Spalte) und liefert die Top N."""
    with read_lock() as conn:
        rows = conn.execute(
            "SELECT artists, total_listen_ms FROM tracks WHERE artists IS NOT NULL AND artists != ''"
        ).fetchall()
    return _top_by_name(rows, "artists", "artist", n)


# DEF: Top-N Tracks nach Wiedergabe-Anzahl
def top_tracks_by_plays(n):
    """Liefert die N meistgespielten Tracks (aus der History, mit Track-Zusatzinfos)."""
    with read_lock() as conn:
        rows = conn.execute(
            """
            SELECT h.track_id, h.track_name, h.artists, h.album,
                   COUNT(*) AS play_count,
                   SUM(h.total_listen_ms) AS total_listen_ms,
                   t.album_cover_url, t.spotify_url
            FROM history h
            LEFT JOIN tracks t ON h.track_id = t.track_id
            GROUP BY h.track_id
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
    return [dict(row) for row in rows]


# SECTION: - Gesamt-Statistik -


# DEF: Gesamt-Statistik der Datenbank (für /stats)
def get_db_stats():
    """Liefert die Gesamt-Statistik der Datenbank (Shape des /stats-Endpoints)."""
    with read_lock() as conn:
        total_tracks = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        total_listen_ms = conn.execute(
            "SELECT COALESCE(SUM(total_listen_ms), 0) FROM tracks"
        ).fetchone()[0]
        total_plays = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
        unique_albums = conn.execute(
            "SELECT COUNT(DISTINCT album) FROM tracks WHERE album IS NOT NULL AND album != ''"
        ).fetchone()[0]
        first_seen = conn.execute("SELECT MIN(added_at) FROM tracks").fetchone()[0]
        last_seen = conn.execute("SELECT MAX(last_seen) FROM tracks").fetchone()[0]
        artist_genre_rows = conn.execute("SELECT artists, genres FROM tracks").fetchall()

    unique_artists: set[str] = set()
    unique_genres: set[str] = set()
    for row in artist_genre_rows:
        unique_artists.update(_split_names(row["artists"]))
        unique_genres.update(_split_names(row["genres"], ignore=(_GENRE_PLACEHOLDER,)))

    avg_listen_ms = (total_listen_ms // total_tracks) if total_tracks else 0
    return {
        "total_tracks": total_tracks,
        "total_listen_ms": total_listen_ms,
        "total_listen_time": format_dd_hh_mm(total_listen_ms),
        "total_plays": total_plays,
        "unique_artists": len(unique_artists),
        "unique_albums": unique_albums,
        "unique_genres": len(unique_genres),
        "avg_listen_ms_per_track": avg_listen_ms,
        "first_seen": format_added_at(first_seen),
        "last_seen": format_added_at(last_seen),
    }


# SECTION: - Zeitraum-Queries (CLI) -
# Zeitraum-Auswertungen kommen aus `history`, nicht aus `tracks`: `tracks.total_listen_ms`
# ist über die gesamte Zeit kumuliert, nur `history` hält fest, *wann* gehört wurde.
# Die Grenzen sind fertige UTC-Zeitstempel ('YYYY-MM-DD HH:MM:SS') eines halboffenen
# Intervalls [start, end); die Umrechnung aus lokaler Zeit macht der Aufrufer.

# CONFIG: Sortierungen für top_tracks_in_period (Allow-List statt SQL aus Parametern)
_PERIOD_TRACK_ORDER = {
    "listen": "total_listen_ms DESC",
    "plays": "play_count DESC, total_listen_ms DESC",
}


# DEF: WHERE-Bedingung für einen Zeitraum
def _range_condition(start, end, column="played_at"):
    """Baut die SQL-Bedingung + Parameter für das Intervall [start, end) auf `column`.

    Verglichen wird direkt auf der Spalte (ohne Funktionsaufruf), damit SQLite den
    Index auf `history.played_at` nutzen kann. `column` stammt nur aus diesem Modul.

    Args:
        start: Untergrenze (inklusiv) als UTC-Zeitstempel, None = offen.
        end: Obergrenze (exklusiv) als UTC-Zeitstempel, None = offen.
        column: Zu vergleichende Zeitstempel-Spalte.

    Returns:
        tuple[str, list]: (Bedingung für WHERE, Parameter).
    """
    conditions: list[str] = []
    params: list = []
    if start:
        conditions.append(f"{column} >= ?")
        params.append(start)
    if end:
        conditions.append(f"{column} < ?")
        params.append(end)
    return (" AND ".join(conditions) or "1"), params


# DEF: Statistik für einen Zeitraum
def get_period_stats(start, end):
    """Liefert die Statistik eines Zeitraums, analog zu `get_db_stats()`.

    Eine „Wiedergabe" ist eine `history`-Zeile (Session) — gezählt wie in `/stats`.

    Args:
        start: Untergrenze (inklusiv) als UTC-Zeitstempel, None = offen.
        end: Obergrenze (exklusiv) als UTC-Zeitstempel, None = offen.

    Returns:
        dict: Hörzeit, Wiedergaben, verschiedene Tracks/Artists/Alben/Genres, im Zeitraum
        neu hinzugekommene Tracks sowie erste/letzte Wiedergabe (UTC, roh aus der DB).
    """
    where, params = _range_condition(start, end)
    added_where, added_params = _range_condition(start, end, column="added_at")
    with read_lock() as conn:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(total_listen_ms), 0) AS listen_ms,
                   COUNT(*) AS plays,
                   COUNT(DISTINCT track_id) AS tracks,
                   COUNT(DISTINCT NULLIF(album, '')) AS albums,
                   MIN(played_at) AS first_played_at,
                   MAX(played_at) AS last_played_at
            FROM history WHERE {where}
            """,
            params,
        ).fetchone()
        artist_rows = conn.execute(
            f"SELECT DISTINCT artists FROM history WHERE {where}", params
        ).fetchall()
        # Genres stehen nur in `tracks` — daher über die im Zeitraum gehörten Track-IDs.
        genre_rows = conn.execute(
            f"SELECT genres FROM tracks WHERE track_id IN "
            f"(SELECT track_id FROM history WHERE {where})",
            params,
        ).fetchall()
        new_tracks = conn.execute(
            f"SELECT COUNT(*) FROM tracks WHERE {added_where}", added_params
        ).fetchone()[0]

    unique_artists: set[str] = set()
    for artist_row in artist_rows:
        unique_artists.update(_split_names(artist_row["artists"]))
    unique_genres: set[str] = set()
    for genre_row in genre_rows:
        unique_genres.update(_split_names(genre_row["genres"], ignore=(_GENRE_PLACEHOLDER,)))

    listen_ms = row["listen_ms"]
    return {
        "total_listen_ms": listen_ms,
        "total_listen_time": format_dd_hh_mm(listen_ms),
        "total_plays": row["plays"],
        "unique_tracks": row["tracks"],
        "new_tracks": new_tracks,
        "unique_artists": len(unique_artists),
        "unique_albums": row["albums"],
        "unique_genres": len(unique_genres),
        "first_played_at": row["first_played_at"],
        "last_played_at": row["last_played_at"],
    }


# DEF: Top-N Tracks eines Zeitraums (nach Hörzeit oder Wiedergaben)
def top_tracks_in_period(start, end, n, by="listen"):
    """Liefert die Top-N-Tracks eines Zeitraums; Shape wie `top_tracks_by_plays()`.

    Args:
        start: Untergrenze (inklusiv) als UTC-Zeitstempel, None = offen.
        end: Obergrenze (exklusiv) als UTC-Zeitstempel, None = offen.
        n: Anzahl der Einträge.
        by: "listen" (Hörzeit) oder "plays" (Anzahl Wiedergaben).

    Returns:
        list[dict]: Tracks mit `play_count` und `total_listen_ms` im Zeitraum.

    Raises:
        ValueError: Bei unbekannter Sortierung.
    """
    order = _PERIOD_TRACK_ORDER.get(by)
    if order is None:
        raise ValueError(f"Unbekannte Sortierung: {by}")
    # Ohne Hörzeit ist ein Track kein „Top-Track nach Hörzeit" — analog zu den Artists/Genres.
    having = "HAVING total_listen_ms > 0" if by == "listen" else ""
    where, params = _range_condition(start, end, column="h.played_at")
    with read_lock() as conn:
        rows = conn.execute(
            f"""
            SELECT h.track_id, h.track_name, h.artists, h.album,
                   COUNT(*) AS play_count,
                   SUM(h.total_listen_ms) AS total_listen_ms,
                   t.album_cover_url, t.spotify_url
            FROM history h
            LEFT JOIN tracks t ON h.track_id = t.track_id
            WHERE {where}
            GROUP BY h.track_id
            {having}
            ORDER BY {order}
            LIMIT ?
            """,
            [*params, n],
        ).fetchall()
    return [dict(row) for row in rows]


# DEF: Top-N Artists eines Zeitraums
def top_artists_in_period(start, end, n):
    """Aggregiert die Hörzeit pro Künstler im Zeitraum; Shape wie `top_artists_by_listen()`."""
    where, params = _range_condition(start, end)
    with read_lock() as conn:
        rows = conn.execute(
            f"SELECT artists, total_listen_ms FROM history "
            f"WHERE {where} AND artists IS NOT NULL AND artists != ''",
            params,
        ).fetchall()
    return _top_by_name(rows, "artists", "artist", n)


# DEF: Top-N Genres eines Zeitraums
def top_genres_in_period(start, end, n):
    """Aggregiert die Hörzeit pro Genre im Zeitraum; Shape wie `top_genres_by_listen()`."""
    where, params = _range_condition(start, end, column="h.played_at")
    with read_lock() as conn:
        rows = conn.execute(
            f"""
            SELECT t.genres, SUM(h.total_listen_ms) AS total_listen_ms
            FROM history h
            JOIN tracks t ON t.track_id = h.track_id
            WHERE {where} AND t.genres IS NOT NULL AND t.genres != ''
            GROUP BY h.track_id
            """,
            params,
        ).fetchall()
    return _top_by_name(rows, "genres", "genre", n, ignore=(_GENRE_PLACEHOLDER,))


# DEF: History eines Zeitraums auflisten
def list_history_in_period(start, end, limit):
    """Liefert die neuesten Hör-Sessions eines Zeitraums; Shape wie `list_history()`.

    Args:
        start: Untergrenze (inklusiv) als UTC-Zeitstempel, None = offen.
        end: Obergrenze (exklusiv) als UTC-Zeitstempel, None = offen.
        limit: Maximale Anzahl Einträge.

    Returns:
        tuple[list[dict], int]: (Einträge, absteigend nach Zeitpunkt; Gesamtanzahl Treffer).
    """
    where, params = _range_condition(start, end)
    with read_lock() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM history WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM history WHERE {where} ORDER BY played_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [dict(row) for row in rows], total
