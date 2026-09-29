"""Verwaltet die Anwendungskonfiguration und stellt Pfade zu Ressourcen bereit.

Lädt Standardwerte und ermöglicht das Speichern von Benutzereinstellungen.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# SECTION: - Globale Pfad-Definitionen -


# DEF: Projekt-Wurzel ermitteln
def _find_project_root():
    """Sucht die Projekt-Wurzel anhand der pyproject.toml als Marker.

    Läuft von dieser Datei aus aufwärts — robust gegen editable installs und
    beliebige Aufruf-Verzeichnisse. Fallback: feste Parent-Zählung im src-Layout
    (common → spotify-db → src → Root).

    Returns:
        Path: Absoluter Pfad zur Projekt-Wurzel.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return here.parents[3]


# CONFIG: Ermittlung des Projekt-Wurzelverzeichnisses
PROJECT_ROOT = _find_project_root()

# CONFIG: Pfad zur zentralen Konfigurationsdatei
CONFIG_PATH = PROJECT_ROOT / "config.json"

# STATE: In-Memory Cache für die Konfiguration
_cached_config = None


# SECTION: - Kern-Logik (Laden & Speichern) -


# DEF: Konfiguration laden
def load_config(force_reload=False):
    """Lädt die Konfiguration aus der JSON-Datei oder gibt Standardwerte zurück.

    Nutzt einen In-Memory-Cache, um Dateizugriffe zu minimieren.

    Args:
        force_reload (bool): Wenn True, wird die Datei zwingend neu von der Festplatte gelesen.

    Returns:
        dict: Die aktuelle Konfiguration.
    """
    global _cached_config

    if _cached_config is not None and not force_reload:
        return _cached_config

    # STATE: Definition der Standardwerte für die Anwendung
    default_config = {
        "track_base_info": True,
        "track_artist_details": True,
        "clear_on_new_song": True,
        "show_status_header": True,
        "show_stats": True,
        # Erlaubte CORS-Origins der API. "*" ist vertretbar, weil jede Antwort
        # key-geschützt ist; bei Bedarf auf konkrete Origins einschränken.
        "cors_origins": ["*"],
        # Statischer Web-Server (serviert den public/-Ordner im Heimnetz).
        # Bind-Adresse und Port; per .env (HOST/PORT) überschreibbar.
        "web_host": "0.0.0.0",
        "web_port": 15002,
        # Optionales Remote-Backup: kopiert beim Tracker-Stop einen sauberen
        # DB-Snapshot per scp auf den Rechner (passwortlose SSH-Key-Auth nötig).
        # Standardmäßig aus; nur host/user/dest ausfüllen und enabled=true setzen.
        "remote_backup": {
            "enabled": False,
            "host": "",
            "user": "",
            "port": 22,
            "dest": "",
            "identity_file": "",
        },
        "paths": {
            "data_dir": "data",
            # Statisches Web-Verzeichnis (im Heimnetz ausgeliefert)
            "public_dir": "public",
            "lock_file": "data/tracker.lock",
            "api_lock_file": "data/api.lock",
            "web_lock_file": "data/web.lock",
            "log_file": "data/tracker.log",
            "spotify_cache": "data/.spotify_cache",
            "status_file": "data/status.json",
            "resync_flag": "data/resync.flag",  # Trigger für sofortigen Re-Poll
            "db_file": "data/database/spotify.db",
            # Gerätespezifisch — enthält api_keys
            "local_db_file": "data/database/local.db",
            # Extern befüllte Enrichment-DB (BPM/Key/Lyrics …), nur lesend
            "songs_db_file": "data/database/songs.db",
            "backup_dir": "data/database/backups",
        },
    }

    try:
        # BRIDGE: Prüfung der Dateiexistenz im Dateisystem
        if not CONFIG_PATH.exists():
            _cached_config = default_config
            return _cached_config

        # BRIDGE: Laden der externen Konfigurationsdatei
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)

            # STATE: Tiefere Zusammenführung für das "paths"-Dictionary
            if "paths" in data:
                default_config["paths"].update(data["paths"])
                del data["paths"]

            # Mergen der restlichen Top-Level Einstellungen
            _cached_config = {**default_config, **data}
            return _cached_config

    except Exception as e:
        print(f"Fehler beim Laden der config.json: {e}")
        _cached_config = default_config
        return _cached_config


# DEF: Einzelnen Schlüssel in config.json setzen
def set_config_value(key, value):
    """Setzt genau einen Top-Level-Schlüssel in der config.json.

    Geschrieben wird nur, was schon in der Datei steht, plus dieser Schlüssel —
    nicht die zusammengeführte Konfiguration. Sonst landeten alle eingebauten
    Defaults (inkl. sämtlicher Pfade) in der Datei und spätere Änderungen an den
    Defaults im Code erreichten diese Installation nie mehr.

    Args:
        key (str): Der Top-Level-Schlüssel, z. B. "default".
        value: Der zu speichernde (JSON-serialisierbare) Wert.

    Returns:
        bool: True, wenn die Datei geschrieben wurde.
    """
    global _cached_config
    try:
        # BRIDGE: Bestehende Datei lesen — ist sie kaputt, lieber abbrechen als überschreiben
        data = {}
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
        data[key] = value

        # BRIDGE: Schreiben der Daten in das Dateisystem
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        # Cache verwerfen, damit load_config() im selben Prozess den neuen Wert sieht
        _cached_config = None
        return True
    except Exception as e:
        print(f"Fehler beim Speichern der config.json: {e}")
        return False


# SECTION: - Ressourcen-Auflösung -


# DEF: Pfadauflösung
def get_path(key):
    """Löst einen Pfad-Key aus der Konfiguration in einen absoluten Pfad auf.

    Args:
        key (str): Der Schlüssel des Pfades in der Konfiguration.

    Returns:
        Path|None: Absoluter Pfad oder None, falls der Key nicht existiert.
    """
    # STATE: Abrufen des aktuellen Pfad-Zustands aus der Konfiguration
    config = load_config()
    paths = config.get("paths", {})
    path_str = paths.get(key)

    if not path_str:
        return None

    # STATE: Berechnung des absoluten Pfades basierend auf dem Projekt-Root
    return PROJECT_ROOT / path_str


# MARK: - Spezialisierte Pfad-Getter -


# DEF: Log-Pfad
def get_log_path():
    """Gibt den Pfad zur Log-Datei zurück."""
    return get_path("log_file")


# DEF: Lock-Pfad
def get_lock_path():
    """Gibt den Pfad zur Tracker-Lock-Datei zurück."""
    return get_path("lock_file")


# DEF: API-Lock-Pfad
def get_api_lock_path():
    """Gibt den Pfad zur API-Lock-Datei zurück."""
    return get_path("api_lock_file")


# DEF: Web-Lock-Pfad
def get_web_lock_path():
    """Gibt den Pfad zur Web-Server-Lock-Datei zurück."""
    return get_path("web_lock_file")


# DEF: Public-Verzeichnis (statischer Web-Server)
def get_public_dir():
    """Gibt den Pfad zum statischen Web-Verzeichnis zurück.

    Priorität: `SERVE_PATH` aus der Umgebung/`.env` > `paths.public_dir` aus
    `config.json` > `public`. Relative `SERVE_PATH`-Werte werden gegen die
    Projekt-Wurzel aufgelöst.

    Returns:
        Path: Absoluter Pfad zum auszuliefernden Verzeichnis.
    """
    load_dotenv()
    serve_path = os.environ.get("SERVE_PATH")
    if serve_path and serve_path.strip():
        p = Path(serve_path.strip())
        return p if p.is_absolute() else (PROJECT_ROOT / p)
    return get_path("public_dir") or (PROJECT_ROOT / "public")


# DEF: Web-Server-Host
def get_web_host():
    """Gibt die Bind-Adresse des statischen Web-Servers zurück.

    Priorität: `HOST` aus der Umgebung/`.env` > `web_host` aus `config.json` >
    `0.0.0.0` (lokal + Heimnetz).

    Returns:
        str: Die Bind-Adresse.
    """
    load_dotenv()
    env_host = os.environ.get("HOST")
    if env_host and env_host.strip():
        return env_host.strip()
    return load_config().get("web_host", "0.0.0.0")


# DEF: Web-Server-Port
def get_web_port():
    """Gibt den Port des statischen Web-Servers zurück.

    Priorität: `PORT` aus der Umgebung/`.env` > `web_port` aus `config.json` > 15002.
    Ungültige (nicht-numerische) `PORT`-Werte werden ignoriert.

    Returns:
        int: Der Port, auf dem der Web-Server bindet.
    """
    load_dotenv()
    env_port = os.environ.get("PORT")
    if env_port and env_port.strip().isdigit():
        return int(env_port)
    return load_config().get("web_port", 15002)


# DEF: Status-Pfad
def get_status_path():
    """Gibt den Pfad zur Status-JSON-Datei zurück."""
    return get_path("status_file")


# DEF: Daten-Verzeichnis
def get_data_dir():
    """Gibt den Pfad zum Datenverzeichnis zurück."""
    return get_path("data_dir")


# DEF: Spotify Cache-Pfad
def get_cache_path():
    """Gibt den Pfad zur Spotify-Cache-Datei (.spotify_cache) zurück."""
    return get_path("spotify_cache")


# DEF: Resync-Flag-Pfad
def get_resync_flag_path():
    """Gibt den Pfad zur Resync-Flag-Datei zurück (Trigger für sofortigen Tracker-Re-Poll)."""
    return get_path("resync_flag")


# DEF: SQLite-DB-Pfad
def get_db_path():
    """Gibt den Pfad zur SQLite-Datenbank zurück."""
    return get_path("db_file")


# DEF: Lokale-DB-Pfad (gerätespezifisch, enthält api_keys)
def get_local_db_path():
    """Gibt den Pfad zur gerätespezifischen lokalen Datenbank zurück."""
    return get_path("local_db_file")


# DEF: Songs-DB-Pfad (extern befüllte Enrichment-DB, nur lesend)
def get_songs_db_path():
    """Gibt den Pfad zur externen Songs-Enrichment-Datenbank (BPM/Key/Lyrics …) zurück."""
    return get_path("songs_db_file")


# DEF: Backup-Verzeichnis
def get_backup_dir():
    """Gibt den Pfad zum Backup-Verzeichnis zurück."""
    return get_path("backup_dir")


# DEF: Remote-Backup-Konfiguration (scp auf den Rechner)
def get_remote_backup_config():
    """Gibt die Remote-Backup-Konfiguration zurück (scp eines DB-Snapshots auf den Rechner).

    Deep-merge über die Defaults, sodass config.json nur abweichende Keys setzen muss.

    Returns:
        dict: Keys `enabled`, `host`, `user`, `port`, `dest`, `identity_file`.
    """
    defaults = {
        "enabled": False,
        "host": "",
        "user": "",
        "port": 22,
        "dest": "",
        "identity_file": "",
    }
    cfg = load_config().get("remote_backup", {})
    return {**defaults, **(cfg if isinstance(cfg, dict) else {})}
