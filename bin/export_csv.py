import csv
import sqlite3
from pathlib import Path

from spotify_db.common.config import get_db_path


def export_to_csv():
    """Exportiert alle Tracks aus der Datenbank in eine CSV-Datei (tracks.csv)."""
    db_path = get_db_path()
    if db_path is None or not db_path.exists():
        print(f"Fehler: Datenbank nicht gefunden unter {db_path}")
        return

    # Verbindung zur Datenbank herstellen
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Tracks abrufen
    cursor.execute("SELECT track_name, artists, track_id FROM tracks")
    rows = cursor.fetchall()

    output_file = Path("tracks.csv")
    with output_file.open(mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Neues Header-Format
        writer.writerow(["id", "song", "artist"])

        for row in rows:
            song = row["track_name"] or ""

            # Die DB speichert Artists meist kommagetrennt (z.B. "The Weeknd, Daft Punk").
            # Die Vorlage nutzt ein Semikolon für mehrere Artists, also ersetzen wir das.
            artists = row["artists"] or ""
            artists = artists.replace(", ", ";")

            track_id = row["track_id"] or ""

            # Zeile schreiben
            writer.writerow([track_id, song, artists])

    print(f"Erfolgreich {len(rows)} Songs aus der Datenbank in '{output_file}' exportiert.")
    conn.close()


if __name__ == "__main__":
    export_to_csv()
