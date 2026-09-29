"""Lese-Logik der Datenbank (Queries + Format-Helfer).

Alle Funktionen sind reine Reads über `database.read_lock()` — Schreibzugriffe
leben in `database.py`. Die API-Routen (`spotify_db.api.routes`) bleiben dünn und
rufen ausschließlich hierher; die Antwort-Shapes der Endpoints werden also von
diesen Funktionen bestimmt.
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

    genre_stats: dict[str, int] = {}
    for row in rows:
        listen_ms = row["total_listen_ms"] or 0
        if listen_ms == 0:
            continue
        for g in row["genres"].split(","):
            g = g.strip()
            if g and g != _GENRE_PLACEHOLDER:
                genre_stats[g] = genre_stats.get(g, 0) + listen_ms

    sorted_genres = sorted(genre_stats.items(), key=lambda x: x[1], reverse=True)[:n]
    return [
        {"genre": g, "total_listen_ms": ms, "total_listen_time": format_hh_mm_ss(ms)}
        for g, ms in sorted_genres
    ]


# DEF: Top-N Artists nach Hörzeit
def top_artists_by_listen(n):
    """Aggregiert die Hörzeit pro Künstler (kommagetrennte Spalte) und liefert die Top N."""
    with read_lock() as conn:
        rows = conn.execute(
            "SELECT artists, total_listen_ms FROM tracks WHERE artists IS NOT NULL AND artists != ''"
        ).fetchall()

    artist_stats: dict[str, int] = {}
    for row in rows:
        listen_ms = row["total_listen_ms"] or 0
        if listen_ms == 0:
            continue
        for a in row["artists"].split(","):
            a = a.strip()
            if a:
                artist_stats[a] = artist_stats.get(a, 0) + listen_ms

    sorted_artists = sorted(artist_stats.items(), key=lambda x: x[1], reverse=True)[:n]
    return [
        {"artist": a, "total_listen_ms": ms, "total_listen_time": format_hh_mm_ss(ms)}
        for a, ms in sorted_artists
    ]


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
        if row["artists"]:
            unique_artists.update(a.strip() for a in row["artists"].split(",") if a.strip())
        if row["genres"]:
            unique_genres.update(
                g.strip()
                for g in row["genres"].split(",")
                if g.strip() and g.strip() != _GENRE_PLACEHOLDER
            )

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
