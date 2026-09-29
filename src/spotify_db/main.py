"""Spotify Tracker Manager — einheitlicher CLI-Einstiegspunkt.

Startet und stoppt Tracker, API- und Web-Server als unabhängige Subprozesse.

Beispiele (alternativ: python -m spotify_db …):
  spotify-db --run                   # Tracker + API + Web-Server starten
  spotify-db --run --tracker         # nur Tracker starten
  spotify-db --run --api             # nur API starten
  spotify-db --run --web             # nur Web-Server starten
  spotify-db --stop                  # alles stoppen
  spotify-db --stop --api            # nur API stoppen
  spotify-db --status                # Status aller drei Prozesse anzeigen
"""

import argparse
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from spotify_db.common.config import (
    get_api_lock_path,
    get_lock_path,
    get_log_path,
    get_web_lock_path,
    load_config,
    set_config_value,
)
from spotify_db.common.status import read_status

# SECTION: - Prozess-Helfer -


def _pid_alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
            ).stdout
            return str(pid) in out
        else:
            os.kill(pid, 0)
            return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_pid(lock_path: Path | None) -> int | None:
    if lock_path is None or not lock_path.exists():
        return None
    try:
        pid_str = lock_path.read_text().strip()
        return int(pid_str) if pid_str.isdigit() else None
    except Exception:
        return None


def _is_running(lock_path) -> int | None:
    """Gibt PID zurück wenn Prozess läuft, sonst None."""
    pid = _read_pid(lock_path)
    if pid and _pid_alive(pid):
        return pid
    return None


def _out_path(label: str) -> Path | None:
    """Gibt den Pfad für stdout/stderr eines Subprozesses zurück (neben der Log-Datei).

    Bewusst NICHT die Log-Datei selbst: der Tracker rotiert die per
    RotatingFileHandler, und ein zweiter offener Schreib-Handle darauf würde
    nach der Rotation in die umbenannte Datei weiterschreiben. Zudem stand früher
    jede Log-Zeile doppelt drin (Datei-Handler plus umgeleiteter StreamHandler).

    Args:
        label (str): Prozess-Label, z.B. "Tracker".

    Returns:
        Path|None: `<datenverzeichnis>/<label>.out`, oder None ohne Log-Pfad.
    """
    log_path = get_log_path()
    if log_path is None:
        return None
    slug = "".join(c for c in label.lower() if c.isalnum()) or "prozess"
    return log_path.with_name(f"{slug}.out")


def _spawn(label: str, cmd: list[str]):
    if os.name == "nt":
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        out_path = _out_path(label)
        if out_path:
            # Eine Generation aufheben: der Traceback eines Absturzes überlebt genau
            # einen Neustart — danach wächst die Datei nicht unbegrenzt weiter.
            if out_path.exists():
                out_path.replace(out_path.with_name(f"{out_path.name}.1"))
            with out_path.open("w") as out_file:
                subprocess.Popen(cmd, stdout=out_file, stderr=out_file, start_new_session=True)
        else:
            subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
    print(f"{label} gestartet.")


def _stop(label: str, lock_path: Path | None, wait_for_exit: bool = False):
    if lock_path is None:
        print(f"{label} läuft nicht (kein Lock-Pfad konfiguriert).")
        return

    pid = _read_pid(lock_path)

    if pid is None:
        print(f"{label} läuft nicht.")
        lock_path.unlink(missing_ok=True)
        return

    if not _pid_alive(pid):
        print(f"{label} läuft nicht (verwaiste Lock-Datei).")
        lock_path.unlink(missing_ok=True)
        return

    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
            # Auf sauberes Beenden warten: der Tracker macht beim Stop ein finales Backup
            # (lokal + optional scp).
            # Bis zu 60s; danach läuft der Upload ggf. noch nach (Prozess räumt sein Lock selbst auf).
            if wait_for_exit:
                print(f"Warte auf sauberes Beenden von {label} (finales Backup)...")
                for _ in range(600):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.1)
        print(f"{label} (PID {pid}) gestoppt.")
    except Exception as e:
        print(f"Fehler beim Stoppen von {label}: {e}")

    lock_path.unlink(missing_ok=True)


# SECTION: - Befehle -


# DEF: Tracker/API/Web starten
def cmd_run(do_tracker: bool, do_api: bool, do_web: bool):
    """Startet Tracker, API- und/oder Web-Server als detachte Subprozesse.

    Args:
        do_tracker (bool): Tracker starten.
        do_api (bool): API starten.
        do_web (bool): Statischen Web-Server starten.
    """
    if do_tracker:
        if _is_running(get_lock_path()):
            print("Tracker läuft bereits.")
        else:
            _spawn("Tracker", [sys.executable, "-m", "spotify_db.spotify.tracker"])

    if do_api:
        if _is_running(get_api_lock_path()):
            print("API läuft bereits.")
        else:
            _spawn("API", [sys.executable, "-m", "spotify_db.api.app"])

    if do_web:
        if _is_running(get_web_lock_path()):
            print("Web-Server läuft bereits.")
        else:
            _spawn("Web-Server", [sys.executable, "-m", "spotify_db.web.server"])


# DEF: Tracker/API/Web stoppen
def cmd_stop(do_tracker: bool, do_api: bool, do_web: bool):
    """Stoppt Tracker, API- und/oder Web-Server per SIGTERM.

    Args:
        do_tracker (bool): Tracker stoppen (wartet auf das finale Backup).
        do_api (bool): API stoppen.
        do_web (bool): Web-Server stoppen.
    """
    if do_tracker:
        _stop("Tracker", get_lock_path(), wait_for_exit=True)
    if do_api:
        _stop("API", get_api_lock_path())
    if do_web:
        _stop("Web-Server", get_web_lock_path())


# DEF: Manuelles Backup
def cmd_backup():
    """Führt einen vollständigen Backup-Zyklus aus (Integritätsprüfung, lokales Backup)."""
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    from spotify_db.db.backup import run_backup

    ok = run_backup()
    print("Backup erfolgreich." if ok else "Backup fehlgeschlagen.")


# DEF: Status aller Prozesse anzeigen
def cmd_status():
    """Zeigt PID/Liveness von Tracker, API und Web-Server sowie den aktuellen Track an."""
    tracker_pid = _is_running(get_lock_path())
    api_pid = _is_running(get_api_lock_path())
    web_pid = _is_running(get_web_lock_path())

    t_info = f"läuft  (PID {tracker_pid})" if tracker_pid else "gestoppt"
    a_info = f"läuft  (PID {api_pid})" if api_pid else "gestoppt"
    w_info = f"läuft  (PID {web_pid})" if web_pid else "gestoppt"
    print(f"Tracker : {t_info}")
    print(f"API     : {a_info}")
    print(f"Web     : {w_info}")

    if not tracker_pid:
        return

    data = read_status()
    if not data:
        return

    # MARK: - Fehlerzustand vor "kein Track" prüfen -
    # Sonst meldet die CLI "Kein Track aktiv", obwohl Spotify die Anfragen ablehnt.
    api_error = data.get("api_error")
    if api_error:
        status = api_error.get("status")
        label = f"Spotify-API {status}" if status else "Spotify-API"
        print(f"\n⚠️  {label}: {api_error.get('message', 'Unbekannter Fehler')}")
        if status == 403:
            print("   → Der Account, dem die App gehört, braucht ein aktives Premium-Abo.")
        return

    try:
        track = data.get("track_data") or {}
        name = track.get("name")
        if not name:
            print("\nKein Track aktiv.")
            return

        artists = track.get("artists", ["?"])
        artist = artists[0] if artists else "?"
        progress_ms = track.get("progress_ms", 0)
        duration_ms = track.get("duration_ms", 0)
        p_min, p_sec = divmod(progress_ms // 1000, 60)
        d_min, d_sec = divmod(duration_ms // 1000, 60)
        symbol = "▶" if track.get("is_playing") else "⏸"
        rank = data.get("track_rank", 0)
        print(f"\n{symbol}  {name} — {artist}")
        print(f"    [{p_min:02}:{p_sec:02} / {d_min:02}:{d_sec:02}]  Rang #{rank}")
    except Exception:
        pass


# SECTION: - Einstiegspunkt -


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Spotify Tracker Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ohne --tracker/--api/--web betreffen --run und --stop alle Komponenten.\n"
            "Default-Befehl setzen:  spotify-db --set-default --run --tracker --api --web\n"
            "Beispiele:\n"
            "  spotify-db\n"
            "  spotify-db --run\n"
            "  spotify-db --run --tracker\n"
            "  spotify-db --stop --api\n"
            "  spotify-db --status"
        ),
    )
    parser.add_argument("--run", action="store_true", help="Startet die ausgewählten Komponenten")
    parser.add_argument("--stop", action="store_true", help="Stoppt die ausgewählten Komponenten")
    parser.add_argument("--status", action="store_true", help="Zeigt den Status aller Prozesse")
    parser.add_argument(
        "--backup", action="store_true", help="Erstellt ein lokales Datenbank-Backup"
    )
    parser.add_argument("--tracker", action="store_true", help="Wählt den Tracker aus")
    parser.add_argument("--api", action="store_true", help="Wählt die API aus")
    parser.add_argument("--web", action="store_true", help="Wählt den Web-Server aus")
    # REMAINDER: nimmt alles nach --set-default als Befehl — auch ein einzelnes
    # Flag wie "--status", das argparse sonst als eigene Option statt als Wert läse.
    parser.add_argument(
        "--set-default",
        nargs=argparse.REMAINDER,
        metavar="BEFEHL",
        help="Speichert einen Default-Befehl (z.B. --set-default --run --tracker --api)",
    )
    return parser


def _execute(args, parser: argparse.ArgumentParser):
    if args.set_default is not None:
        default = " ".join(args.set_default).strip()
        if not default:
            parser.error("--set-default braucht einen Befehl, z.B. --set-default --run")
        if set_config_value("default", default):
            print(f"Default gesetzt: spotify-db  →  spotify-db {default}")
        return

    # Ohne Ziel-Flag betreffen --run/--stop alle Komponenten.
    all_targets = not args.tracker and not args.api and not args.web
    do_tracker = all_targets or args.tracker
    do_api = all_targets or args.api
    do_web = all_targets or args.web

    if args.run:
        cmd_run(do_tracker, do_api, do_web)
    elif args.stop:
        cmd_stop(do_tracker, do_api, do_web)
    elif args.status:
        cmd_status()
    elif args.backup:
        cmd_backup()
    else:
        parser.print_help()


# DEF: CLI-Einstiegspunkt
def main():
    """Parst die CLI-Argumente und führt den gewählten Befehl (oder den Default) aus."""
    parser = _build_parser()
    args = parser.parse_args()

    no_action = not any(
        [
            args.run,
            args.stop,
            args.status,
            args.backup,
            args.set_default is not None,
            args.tracker,
            args.api,
            args.web,
        ]
    )
    if no_action:
        default = load_config().get("default", "").strip()
        if default:
            print(f"Führe Default aus: spotify-db {default}")
            args = parser.parse_args(shlex.split(default))
        else:
            print(
                "Kein Default konfiguriert.\n"
                "Tipp: spotify-db --set-default --run --tracker --api --web"
            )
            return

    _execute(args, parser)


if __name__ == "__main__":
    main()
