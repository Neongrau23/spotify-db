"""Der status.json-Vertrag zwischen Tracker (Schreiber) und API/CLI (Leser).

Die Datei ist die einzige Quelle für Live-Daten (`/live` fragt Spotify NICHT
selbst ab). Der Tracker schreibt sie bei jedem Poll und sekündlich zwischen
den Polls (Dead Reckoning). Lesen und Schreiben laufen ausschließlich über
dieses Modul, damit Pfad, Atomarität und Fehlerverhalten nur an einer Stelle
definiert sind.
"""

import json
import logging
import os
from pathlib import Path

from spotify_db.common.config import get_status_path

logger = logging.getLogger(__name__)


# DEF: Status atomar schreiben (Tracker)
def write_status(payload: dict) -> None:
    """Schreibt den Status-Payload atomar in die status.json.

    Atomar heißt: erst in eine temporäre Datei, dann per replace umbenennen.
    Verhindert, dass ein Leser (API liest jede Sekunde) eine halb geschriebene/
    leere Datei erwischt und an JSONDecodeError scheitert.

    Args:
        payload: Der vollständige Status-Inhalt (Felder definiert der Tracker).
    """
    raw = get_status_path()
    if not raw:
        logger.error("Status-Pfad nicht konfiguriert — Status nicht geschrieben.")
        return
    status_path = Path(raw)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_name(f"{status_path.name}.{os.getpid()}.tmp")
    try:
        with tmp_path.open("w") as f:
            json.dump(payload, f)
        tmp_path.replace(status_path)
    except Exception as e:
        logger.error(f"Fehler beim Schreiben des Status: {e}")
        tmp_path.unlink(missing_ok=True)


# DEF: Status lesen (API, CLI, Playback)
def read_status() -> dict | None:
    """Liest die status.json.

    Returns:
        dict | None: Der Status-Inhalt, oder None wenn die Datei fehlt,
        kein Pfad konfiguriert ist oder sie (kurzzeitig) nicht lesbar war.
    """
    raw = get_status_path()
    if not raw:
        return None
    status_path = Path(raw)
    if not status_path.exists():
        return None
    try:
        with status_path.open() as f:
            return json.load(f)
    except Exception:
        return None
