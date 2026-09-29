"""Backup-Modul für die SQLite-Datenbank.

Erstellt lokale Snapshots und kopiert beim Tracker-Stop optional einen per scp auf
einen anderen Rechner.
"""

import logging
import socket
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from spotify_db.common.config import (
    PROJECT_ROOT,
    get_backup_dir,
    get_data_dir,
    get_db_path,
    get_remote_backup_config,
)

logger = logging.getLogger(__name__)

# CONFIG: Anzahl lokaler Backups die aufbewahrt werden (lokale Bequemlichkeit)
_LOCAL_BACKUP_KEEP = 7

# STATE: Verhindert gleichzeitige Backup-Läufe (z.B. täglicher + manueller)
_backup_lock = threading.Lock()


# SECTION: - Lokales Backup -


def _require_path(path: Path | None, description: str) -> Path:
    """Stellt sicher, dass ein konfigurierter Pfad vorhanden ist.

    Die `get_*_path`-Getter in `common/config.py` geben `None` zurück, falls der
    Key in `config.json` fehlt — praktisch tritt das nicht ein, da alle hier
    genutzten Keys Teil der Default-Config sind. Fail-fast statt `Path(None)`.
    """
    if path is None:
        raise RuntimeError(f"{description} nicht konfiguriert.")
    return path


def _get_backup_dir() -> Path:
    """Gibt das Verzeichnis für lokale Backups zurück und legt es ggf. an."""
    backup_dir = Path(get_backup_dir() or PROJECT_ROOT / "data/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def _integrity_check_file(db_file: Path) -> bool:
    """Führt PRAGMA integrity_check auf einer beliebigen SQLite-Datei aus.

    Returns:
        True wenn die Datenbank intakt ist.
    """
    try:
        conn = sqlite3.connect(str(db_file), timeout=10)
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if result and result[0] == "ok":
            return True
        logger.error(f"Integritätsprüfung fehlgeschlagen ({db_file.name}): {result}")
        return False
    except Exception as e:
        logger.error(f"Fehler bei der Integritätsprüfung ({db_file.name}): {e}")
        return False


def run_integrity_check() -> bool:
    """Führt PRAGMA integrity_check auf der konfigurierten DB aus.

    Returns:
        True wenn die Datenbank intakt ist.
    """
    db_path = _require_path(get_db_path(), "DB-Pfad")
    if not db_path.exists():
        logger.warning("Integritätsprüfung übersprungen: DB-Datei nicht vorhanden.")
        return True
    if _integrity_check_file(db_path):
        logger.info("Integritätsprüfung: OK")
        return True
    return False


def _checkpoint_wal():
    """Schreibt den WAL-Journal zurück in die Hauptdatei (verhindert unkontrolliertes Wachstum)."""
    db_path = _require_path(get_db_path(), "DB-Pfad")
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        conn.close()
        logger.info("WAL-Checkpoint abgeschlossen.")
    except Exception as e:
        # Nicht kritisch — Backup läuft trotzdem
        logger.warning(f"WAL-Checkpoint fehlgeschlagen: {e}")


def _sqlite_backup(src_path: Path, dst_path: Path) -> bool:
    """Kopiert eine SQLite-DB online-sicher via sqlite3.backup() (WAL-tolerant).

    Returns:
        True bei Erfolg; bei Fehler wird die (Teil-)Zieldatei entfernt.
    """
    try:
        src = sqlite3.connect(str(src_path), timeout=10)
        dst = sqlite3.connect(str(dst_path))
        src.backup(dst)
        src.close()
        dst.close()
        return True
    except Exception as e:
        logger.error(f"SQLite-Backup fehlgeschlagen ({dst_path.name}): {e}")
        dst_path.unlink(missing_ok=True)
        return False


def _create_local_backup() -> Path | None:
    """Erstellt eine timestampte SQLite-Kopie via sqlite3.backup() (online-sicher).

    Returns:
        Pfad zum erstellten Backup-File, oder None bei Fehler.
    """
    db_path = _require_path(get_db_path(), "DB-Pfad")
    if not db_path.exists():
        logger.warning("Backup übersprungen: DB-Datei nicht vorhanden.")
        return None

    backup_dir = _get_backup_dir()
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"spotify_{timestamp}.db"

    if not _sqlite_backup(db_path, backup_path):
        return None
    logger.info(f"Lokales Backup erstellt: {backup_path.name}")
    return backup_path


def _rotate_local_backups():
    """Löscht älteste lokale Backups, sodass maximal _LOCAL_BACKUP_KEEP übrig bleiben."""
    backup_dir = _get_backup_dir()
    backups = sorted(backup_dir.glob("spotify_*.db"))
    for old in backups[:-_LOCAL_BACKUP_KEEP] if len(backups) > _LOCAL_BACKUP_KEEP else []:
        try:
            old.unlink()
            logger.info(f"Altes lokales Backup gelöscht: {old.name}")
        except Exception as e:
            logger.warning(f"Konnte altes Backup nicht löschen ({old.name}): {e}")


# SECTION: - Remote-Backup (scp auf den Rechner) -
#
# Kopiert beim Tracker-Stop einen sauberen DB-Snapshot per scp auf den Rechner.
# Braucht weder Mount noch ein Skript auf der Gegenseite — passwortlose SSH-Key-Auth
# (BatchMode) genügt. Standardmäßig aus.


def _tcp_reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    """Prüft schnell, ob der SSH-Port des Rechners erreichbar ist (TCP-Connect)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def push_to_remote() -> bool:
    """Kopiert einen sauberen DB-Snapshot per scp auf den Rechner (best effort).

    Nur aktiv, wenn `remote_backup.enabled` in config.json gesetzt und der Host per
    SSH erreichbar ist. Erstellt einen online-sicheren Snapshot (kein WAL-Ärger) und
    überträgt ihn per scp mit BatchMode (setzt passwortlose SSH-Key-Auth voraus).
    Fehler werden geloggt, aber nie weitergereicht — der Tracker-Stop darf daran
    nicht scheitern.

    Returns:
        True, wenn der Snapshot erfolgreich übertragen wurde.
    """
    cfg = get_remote_backup_config()
    if not cfg.get("enabled"):
        return False

    host, user, dest = cfg.get("host"), cfg.get("user"), cfg.get("dest")
    if not (host and user and dest):
        logger.warning("Remote-Backup aktiviert, aber host/user/dest unvollständig — übersprungen.")
        return False

    port = int(cfg.get("port") or 22)
    identity = cfg.get("identity_file") or ""

    if not _tcp_reachable(host, port):
        logger.info(f"Rechner {host}:{port} nicht erreichbar — Remote-Backup übersprungen.")
        return False

    db_path = _require_path(get_db_path(), "DB-Pfad")
    if not db_path.exists():
        logger.warning("Remote-Backup übersprungen: DB-Datei nicht vorhanden.")
        return False

    # Sauberer Snapshot in eine temporäre Datei — online-sicher, ohne WAL-Reste.
    tmp = _require_path(get_data_dir(), "Daten-Verzeichnis") / ".remote_push.db"
    if not _sqlite_backup(db_path, tmp):
        return False

    remote_dir = dest.rstrip("/")
    target = f"{remote_dir}/spotify.db"
    common_opts = ["-o", "ConnectTimeout=5", "-o", "BatchMode=yes"]
    id_opts = ["-i", identity] if identity else []
    ssh_cmd = ["ssh", "-p", str(port), *common_opts, *id_opts, f"{user}@{host}"]
    scp_cmd = ["scp", "-P", str(port), "-p", *common_opts, *id_opts]
    try:
        # Zielverzeichnis auf dem Rechner sicherstellen (kein Mount nötig).
        subprocess.run(
            [*ssh_cmd, "mkdir", "-p", remote_dir],
            check=True,
            timeout=15,
            capture_output=True,
        )
        subprocess.run(
            [*scp_cmd, str(tmp), f"{user}@{host}:{target}"],
            check=True,
            timeout=120,
            capture_output=True,
        )
        logger.info(f"Remote-Backup auf {host}:{target} kopiert.")
        return True
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace").strip()
        logger.error(f"Remote-Backup fehlgeschlagen: {stderr or e}")
        return False
    except Exception as e:
        logger.error(f"Remote-Backup fehlgeschlagen: {e}")
        return False
    finally:
        tmp.unlink(missing_ok=True)


# SECTION: - Haupt-Einstiegspunkt -


def run_backup() -> bool:
    """Führt einen vollständigen Backup-Zyklus durch (threadsicher).

    Ablauf: Integritätsprüfung → WAL-Checkpoint → lokales Backup → Rotation.

    Returns:
        True wenn das lokale Backup erfolgreich erstellt wurde.
    """
    if not _backup_lock.acquire(blocking=False):
        logger.info("Backup läuft bereits — übersprungen.")
        return False

    try:
        logger.info("Starte Backup-Zyklus...")

        if not run_integrity_check():
            logger.error("Backup abgebrochen: Integritätsprüfung fehlgeschlagen.")
            return False

        _checkpoint_wal()

        if _create_local_backup() is None:
            return False

        _rotate_local_backups()

        logger.info("Backup-Zyklus abgeschlossen.")
        return True
    finally:
        _backup_lock.release()
