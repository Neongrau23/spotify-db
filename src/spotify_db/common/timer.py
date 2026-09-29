"""Berechnet Zeitintervalle für den Tracker-Zyklus."""


def calculate_wait_time(track_data=None):
    """Ermittelt die Wartezeit bis zum nächsten Polling-Intervall.

    Args:
        track_data (dict, optional): Daten des aktuellen Tracks.

    Returns:
        int: Wartezeit in Sekunden (5 bei Wiedergabe, sonst 15).
    """
    if track_data and track_data.get("is_playing"):
        return 5
    return 15
