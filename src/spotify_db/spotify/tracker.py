"""Spotify-Tracker-Loop.

Überwacht den aktuellen Track, speichert Daten in der SQLite-Datenbank und
schreibt sekündliche Status-Updates für die API und externe Widgets.
Wird von main.py als Subprozess gestartet, kann aber auch direkt ausgeführt werden.
"""

import contextlib
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

if os.name == "nt":
    import msvcrt

signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

from spotify_db.common.config import (
    get_data_dir,
    get_lock_path,
    get_log_path,
    get_resync_flag_path,
    load_config,
    set_config_value,
)
from spotify_db.common.display import print_status_header, print_track_display
from spotify_db.common.status import write_status
from spotify_db.common.timer import calculate_wait_time
from spotify_db.db import database, queries
from spotify_db.db.backup import push_to_remote, run_backup
from spotify_db.spotify.auth import SpotifyCredentialsError
from spotify_db.spotify.collector import SpotifyApiError, fetch_current_track, fetch_user_profile

# SECTION: - System Setup -

# CONFIG: Log-Rotation — hält die Datei auf dem Gerät klein (max 4 mal 2 MB).
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3

log_path_val = get_log_path()
if log_path_val is None:
    raise RuntimeError("Log-Pfad nicht konfiguriert.")
log_path = log_path_val
log_path.parent.mkdir(parents=True, exist_ok=True)

# MARK: - Handler-Auswahl: keine doppelten Zeilen im Hintergrund -
# Im Hintergrund leitet main.py stdout/stderr in eine eigene .out-Datei um; ein
# StreamHandler wäre dort nur eine zweite Kopie derselben Zeilen (früher landeten
# beide Wege in tracker.log — daher stand dort jede Zeile doppelt). Auf einem TTY
# (Vordergrund-Debugging) will man die Ausgabe dagegen sehen.
_handlers: list[logging.Handler] = [
    logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
]
if sys.stdout.isatty():
    _handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=_handlers,
)

# spotipy protokolliert jeden HTTP-Fehler selbst auf ERROR-Level. Da wir jede
# SpotifyException als SpotifyApiError auffangen und selbst (dedupliziert) loggen,
# wäre das nur Rauschen — ein dauerhafter 403 füllte so das halbe Log.
logging.getLogger("spotipy").setLevel(logging.CRITICAL)

logger = logging.getLogger(__name__)

# CONFIG: Intervall für das periodische lokale Backup (Sekunden). Reine Crash-Versicherung —
# beim Beenden läuft ohnehin ein finales Backup (siehe finally-Block).
BACKUP_INTERVAL_S = 30 * 60

# CONFIG: Wartezeit nach einem abgelehnten Spotify-Aufruf. Länger als das normale
# Idle-Intervall — solche Fehler (fehlendes Premium, gesperrte App) halten typisch
# Stunden an, und schnelleres Pollen bringt die Freigabe nicht näher.
API_ERROR_WAIT_S = 60

# STATE: Spotify-Nutzerprofil (Anzeigename + Profilbild). Einmalig beim Start geholt
# und in jeden Status-Payload gespiegelt — Account-Daten ändern sich pro Sitzung nicht.
_user_profile = None


# SECTION: - Status-Datei-Schreiber -


# DEF: Status-Datei schreiben (für API)
def update_status_file(
    next_scan_time,
    start_time,
    track_data=None,
    session_total=0,
    session_unique=0,
    db_total=0,
    is_new_in_db=False,
    total_listen_ms=0,
    track_rank=0,
    api_error=None,
):
    """Schreibt den aktuellen Tracker-Zustand atomar in die status.json.

    Args:
        next_scan_time (float): Unix-Zeitpunkt des nächsten Spotify-Polls.
        start_time (float): Unix-Zeitpunkt des Tracker-Starts.
        track_data (dict, optional): Metadaten des aktuellen Tracks.
        session_total (int): Gehörte Songs in dieser Sitzung.
        session_unique (int): Davon neu in der Datenbank.
        db_total (int): Gesamtzahl der Songs in der Datenbank.
        is_new_in_db (bool): Ob der aktuelle Song neu in der DB ist.
        total_listen_ms (int): Gesamthörzeit des aktuellen Tracks.
        track_rank (int): Rang des Tracks nach Hörzeit.
        api_error (dict, optional): `{"status", "message"}`, wenn Spotify den
            letzten Poll abgelehnt hat — sonst None. Unterscheidet für Leser
            "Spotify antwortet nicht" von "es läuft gerade nichts".
    """
    # Die Feld-Belegung definiert der Tracker; Pfad + atomares Schreiben kapselt
    # `common.status` (eine Stelle für den status.json-Vertrag).
    write_status(
        {
            "next_scan_time": next_scan_time,
            "start_time": start_time,
            "session_total": session_total,
            "session_unique": session_unique,
            "db_total": db_total,
            "is_new_in_db": is_new_in_db,
            "track_data": track_data,
            "total_listen_ms": total_listen_ms,
            "track_rank": track_rank,
            "profile": _user_profile,
            "api_error": api_error,
        }
    )


# DEF: "Now Playing" Textdatei schreiben
def update_now_playing_file(track_data):
    """Schreibt eine einzeilige "Song - Artist"-Textdatei für OBS/externe Widgets.

    Args:
        track_data (dict, optional): Metadaten des aktuellen Tracks; None → "Inaktiv".
    """
    data_dir_val = get_data_dir()
    if data_dir_val is None:
        logger.error("Daten-Verzeichnis nicht konfiguriert.")
        return
    tmp_dir = data_dir_val / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    file_path = tmp_dir / "now_playing.txt"

    if not track_data:
        content = "Inaktiv"
    else:
        song = track_data.get("name", "Unbekannt")
        artists_list = track_data.get("artists", [])
        artist = artists_list[0] if artists_list else "Unbekannt"
        content = f"{song} - {artist}"

    try:
        with file_path.open("w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.error(f"Fehler beim Schreiben der Now-Playing Datei: {e}")


# SECTION: - Tracker Kern-Logik -


# DEF: Tracker-Hauptloop starten
def start_tracker():
    """Startet den Tracker-Hauptloop (Polling, Persistenz, Status-Updates, Backups)."""
    lock_path_val = get_lock_path()
    if lock_path_val is None:
        logger.error("Lock-Pfad nicht konfiguriert.")
        sys.exit(1)
    lock_path = lock_path_val

    if lock_path.exists():
        try:
            pid_str = lock_path.read_text().strip()
            if pid_str.isdigit():
                pid = int(pid_str)
                try:
                    os.kill(pid, 0)
                    logger.error(
                        f"Tracker läuft anscheinend schon (PID: {pid}). Bitte zuerst beenden mit --stop."
                    )
                    sys.exit(1)
                except (ProcessLookupError, PermissionError):
                    logger.warning(
                        f"Verwaiste Lock-Datei (PID {pid} nicht mehr aktiv). Bereinige und starte neu."
                    )
                    lock_path.unlink(missing_ok=True)
        except SystemExit:
            raise
        except Exception:
            lock_path.unlink(missing_ok=True)

    database.initialize_db()

    start_time = time.time()
    last_track_id = None
    tracks_since_start = 0
    session_new_songs = 0

    # STATE: Fallback-Werte für den Fehlerzweig — vorinitialisiert, damit ein Poll-Fehler
    # noch vor dem ersten erfolgreichen Durchlauf einen sinnvollen Status schreiben kann.
    track_data = None
    is_new_in_db = False
    current_total_listen_ms = 0
    current_track_rank = 0

    # STATE: Letzter Spotify-Fehlerzustand. `api_error` reist in jeden Status-Payload
    # (None = Spotify antwortet normal); `last_api_error_key` unterdrückt die
    # Log-Wiederholung, solange sich derselbe Fehler bei jedem Poll wiederholt.
    api_error = None
    last_api_error_key = None

    # STATE: Zustand des vorherigen Polls — Basis für die Hörzeit-Berechnung.
    # Gezählt wird nur die zwischen zwei Polls tatsächlich verstrichene Zeit,
    # nicht ein fixer Block pro Poll (sonst bläht jeder Resync die Hörzeit auf).
    last_poll_real_time = None
    last_poll_track_id = None
    last_poll_is_playing = False

    # STATE: Zeitpunkt des letzten Upload-Zyklus — alle BACKUP_INTERVAL_S ausgeführt
    last_backup_time = time.time()

    print("Starte Spotify Tracker...")

    # MARK: - Nutzerprofil einmalig laden (Anzeigename + Profilbild für /live) -
    # Fehlende Zugangsdaten fallen hier als Erstes auf — klar melden und beenden,
    # statt mit einem spotipy-Traceback abzubrechen (noch vor dem Lock-Schreiben).
    global _user_profile
    try:
        _user_profile = fetch_user_profile()
    except SpotifyCredentialsError as e:
        logger.error(str(e))
        sys.exit(1)

    db_total_count = queries.get_total_tracks_count()
    config = load_config()
    print_status_header(config)

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as f:
        f.write(str(os.getpid()))

    resync_flag_path_val = get_resync_flag_path()
    resync_flag_path = (
        resync_flag_path_val
        if resync_flag_path_val is not None
        else lock_path.parent / "resync.flag"
    )
    try:
        while True:
            try:
                track_data = fetch_current_track()

                # STATE: Poll war erfolgreich — ein vorheriger Fehlerzustand ist vorbei.
                if last_api_error_key is not None:
                    logger.info("Spotify antwortet wieder normal.")
                api_error = None
                last_api_error_key = None

                poll_real_time = time.time()
                update_now_playing_file(track_data)

                current_track_id = track_data.get("track_id") if track_data else None
                current_is_playing = bool(track_data and track_data.get("is_playing"))

                # MARK: - Hörzeit-Increment aus tatsächlich verstrichener Zeit -
                # Nur anrechnen, wenn im vorherigen Poll derselbe Track bereits lief —
                # dann gehört das Intervall [letzter Poll, jetzt] dieser Wiedergabe.
                # Bei Track-Wechsel oder Pause-Übergang ist das Increment 0.
                listen_increment_ms = 0
                if (
                    last_poll_real_time is not None
                    and last_poll_is_playing
                    and last_poll_track_id is not None
                    and last_poll_track_id == current_track_id
                ):
                    listen_increment_ms = int((poll_real_time - last_poll_real_time) * 1000)

                is_new_in_db = False
                if track_data:
                    if current_track_id and current_track_id != last_track_id:
                        last_track_id = current_track_id
                        tracks_since_start += 1

                        is_new_in_db = database.save_track_to_db(
                            track_data, is_new_play=True, listen_increment_ms=listen_increment_ms
                        )

                        if is_new_in_db:
                            session_new_songs += 1
                            db_total_count += 1

                        print_track_display(
                            track_data,
                            tracks_since_start,
                            session_new_songs,
                            db_total_count,
                            bool(is_new_in_db),
                            config,
                        )
                    else:
                        database.save_track_to_db(
                            track_data, is_new_play=False, listen_increment_ms=listen_increment_ms
                        )

                # STATE: Poll-Zustand für die Increment-Berechnung des nächsten Polls merken.
                last_poll_real_time = poll_real_time
                last_poll_track_id = current_track_id
                last_poll_is_playing = current_is_playing

                wait_time = calculate_wait_time(track_data)
                next_scan_time = time.time() + wait_time

                current_total_listen_ms = 0
                current_track_rank = 0
                if track_data and track_data.get("track_id"):
                    stats = queries.get_track_stats(track_data["track_id"])
                    current_total_listen_ms = stats["total_listen_ms"]
                    current_track_rank = stats["rank"]

                poll_progress_ms = track_data.get("progress_ms", 0) if track_data else 0
                poll_duration_ms = track_data.get("duration_ms", 0) if track_data else 0
                poll_is_playing = current_is_playing

                update_status_file(
                    next_scan_time,
                    start_time,
                    track_data,
                    tracks_since_start,
                    session_new_songs,
                    db_total_count,
                    is_new_in_db,
                    current_total_listen_ms,
                    current_track_rank,
                )

                # MARK: - Periodisches lokales Backup -
                # Im Hintergrund-Thread, damit der Tick-Loop nicht blockiert.
                if time.time() - last_backup_time >= BACKUP_INTERVAL_S:
                    threading.Thread(target=run_backup, daemon=True, name="backup").start()
                    last_backup_time = time.time()

                # MARK: - Inner Loop: Dead Reckoning + Resync-Listener -
                ticks = int(wait_time * 10)
                for tick in range(ticks):
                    if resync_flag_path.exists():
                        with contextlib.suppress(FileNotFoundError):
                            resync_flag_path.unlink()
                        break

                    if tick > 0 and tick % 10 == 0 and track_data:
                        if poll_is_playing:
                            elapsed_ms = int((time.time() - poll_real_time) * 1000)
                            interp_ms = poll_progress_ms + elapsed_ms
                            if poll_duration_ms:
                                interp_ms = min(interp_ms, poll_duration_ms)
                        else:
                            interp_ms = poll_progress_ms

                        interp_track = {**track_data, "progress_ms": interp_ms}
                        update_status_file(
                            next_scan_time,
                            start_time,
                            interp_track,
                            tracks_since_start,
                            session_new_songs,
                            db_total_count,
                            is_new_in_db,
                            current_total_listen_ms,
                            current_track_rank,
                        )

                    if os.name == "nt" and msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch == b"!":
                            config["show_status_header"] = not config.get(
                                "show_status_header", True
                            )
                            set_config_value("show_status_header", config["show_status_header"])
                        elif ch == b"i":
                            config["show_stats"] = not config.get("show_stats", True)
                            set_config_value("show_stats", config["show_stats"])

                        if ch in [b"!", b"i"] and track_data:
                            print_track_display(
                                track_data,
                                tracks_since_start,
                                session_new_songs,
                                db_total_count,
                                False,
                                config,
                            )

                    time.sleep(0.1)

            # MARK: - Spotify lehnt ab (403 ohne Premium, 401, 5xx) -
            # Eigener Zweig, weil das ein Fehlerzustand ist und kein "nichts läuft":
            # er reist als api_error in die status.json, damit `--status` und `/live`
            # die Ursache zeigen statt "Kein Track aktiv".
            except SpotifyApiError as e:
                api_error = {"status": e.status, "message": e.message}

                error_key = (e.status, e.message)
                if error_key != last_api_error_key:
                    logger.error(f"Spotify lehnt Anfragen ab ({e.status}): {e.message}")
                    last_api_error_key = error_key

                track_data = None
                update_now_playing_file(None)
                update_status_file(
                    time.time() + API_ERROR_WAIT_S,
                    start_time,
                    None,
                    tracks_since_start,
                    session_new_songs,
                    db_total_count,
                    False,
                    0,
                    0,
                    api_error=api_error,
                )
                time.sleep(API_ERROR_WAIT_S)

            except Exception as e:
                logger.error(f"Fehler im Tracker-Loop: {e}")
                update_status_file(
                    time.time() + 15,
                    start_time,
                    track_data,
                    tracks_since_start,
                    session_new_songs,
                    db_total_count,
                    is_new_in_db,
                    current_total_listen_ms,
                    current_track_rank,
                    api_error=api_error,
                )
                time.sleep(15)

    except KeyboardInterrupt:
        print("Tracker wurde vom Benutzer beendet.")
    finally:
        # MARK: - Finales Backup beim Beenden -
        # Best effort — Fehler dürfen das Beenden nicht verhindern.
        try:
            run_backup()
        except Exception as e:
            logger.warning(f"Finales Backup beim Beenden fehlgeschlagen: {e}")
        # MARK: - Remote-Backup: sauberen DB-Stand auf den Rechner kopieren -
        # Nur beim Beenden (nicht im periodischen Zyklus), best effort.
        # No-Op, solange remote_backup in config.json nicht aktiviert ist.
        try:
            push_to_remote()
        except Exception as e:
            logger.warning(f"Remote-Backup beim Beenden fehlgeschlagen: {e}")
        database.close_connection()
        if lock_path.exists():
            lock_path.unlink()
        print("Tracker gestoppt.")


if __name__ == "__main__":
    start_tracker()
