"""Stellt Funktionen zur Terminal-Formatierung und Statusanzeige bereit.

Verwaltet Farben und die visuelle Aufbereitung von Track-Informationen.
"""

import os

from spotify_db.common.config import load_config

# ANSI Farbcodes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def clear_terminal():
    """Leert den aktuellen Terminal-Inhalt."""
    os.system("cls" if os.name == "nt" else "clear")


def format_status(val):
    """Formatiert einen booleschen Wert farblich.

    Args:
        val (bool): Der zu formatierende Wert.

    Returns:
        str: Grün bei True, Rot bei False.
    """
    color = GREEN if val else RED
    return f"{color}{val}{RESET}"


def format_number(num):
    """Formatiert eine Zahl farblich in Grün.

    Args:
        num (int|float): Die zu formatierende Zahl.

    Returns:
        str: Die formatierte Zahl als String.
    """
    return f"{GREEN}{num}{RESET}"


def print_status_header(config=None):
    """Gibt den Header mit dem Status der Konfigurationseinstellungen aus.

    Args:
        config (dict, optional): Aktuelle Konfiguration. Lädt Standard falls None.
    """
    if config is None:
        config = load_config()

    if not config.get("show_status_header", True):
        return

    t = format_status(config.get("track_base_info"))
    a = format_status(config.get("track_artist_details"))
    print(f"Track: {t}, Artist: {a}")


def print_track_display(
    track_data, session_total, session_unique, db_total, is_new_in_db, config=None
):
    """Zeigt Details zum aktuellen Track sowie Statistiken im Terminal an.

    Args:
        track_data (dict): Metadaten des aktuellen Songs.
        session_total (int): Gesamtzahl gehörter Songs in der Sitzung.
        session_unique (int): Anzahl unterschiedlicher Songs in der Sitzung.
        db_total (int): Gesamtzahl der Songs in der Datenbank.
        is_new_in_db (bool): Markierung, ob der Song neu in der DB ist.
        config (dict, optional): Aktuelle Konfiguration.
    """
    if config is None:
        config = load_config()

    if config.get("clear_on_new_song", True):
        clear_terminal()
        print_status_header(config)

    genres = track_data.get("genres", [])
    genres_str = ", ".join(genres) if genres else "Keine Genres gefunden"

    # Zeitformatierung mm:ss
    duration_ms = track_data.get("duration_ms", 0)
    minutes = (duration_ms // 1000) // 60
    seconds = (duration_ms // 1000) % 60
    duration_str = f"{minutes:02}:{seconds:02}"

    if config.get("show_stats", True):
        stats = f"\ninsgesamt gehörte songs: {format_number(session_total)},  "
        stats += f"einzigartige songs: {format_number(session_unique)},  "
        stats += f"alle songs: {format_number(db_total)}"
        print(stats)

    print("-" * 26)

    print(f"Song: {track_data['name']}")
    print(f"Künstler: {', '.join(track_data['artists'])}")
    print(f"Genres: {genres_str}")
    print(f"Dauer: {duration_str}")
    print("-" * 26)

    if is_new_in_db:
        print(f"{GREEN}NEW{RESET}")
