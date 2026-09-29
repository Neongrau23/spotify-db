"""Statischer Datei-Server ohne externe Abhängigkeiten — liefert ein Verzeichnis im Heimnetz.

Ein eigenständiger Prozess (wie Tracker und API), gestartet via `spotify-db --run --web`
bzw. `python -m spotify_db.web.server`. Reine Standardbibliothek (`http.server`): liefert
Directory-Listing, `index.html`, MIME-Typen, HEAD, Trailing-Slash-Redirect und
Pfad-Traversal-Schutz von Haus aus — kein Framework, kein zusätzliches Dependency.

Konfiguration kommt aus der `.env` (mit Fallbacks, siehe `common/config.py`):
  HOST        Bind-Adresse       (Default 0.0.0.0 — lokal + Heimnetz)
  PORT        Bind-Port          (Default 15002)
  SERVE_PATH  Ausgeliefertes Verzeichnis (Default public/)

Bindet ohne Auth und ohne Tunnel. Lock-Datei: `data/web.lock`.
"""

import errno
import mimetypes
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from spotify_db.common.config import (
    get_public_dir,
    get_web_host,
    get_web_lock_path,
    get_web_port,
)

# CONFIG: Endungen, die mimetypes je nach Plattform nicht kennt — für guess_type ergänzt.
_EXTRA_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


# DEF: Request-Handler (stumm)
class _StaticHandler(SimpleHTTPRequestHandler):
    """Statischer Handler ohne Per-Request-Logging.

    `SimpleHTTPRequestHandler` bringt Directory-Listing, index.html, HEAD,
    Trailing-Slash-Redirect (301), MIME-Erkennung (`guess_type` → `mimetypes`) und
    den Pfad-Traversal-Schutz (`translate_path` normalisiert `..` weg) bereits mit —
    hier wird nur das Logging stummgeschaltet.
    """

    def log_message(self, *args, **kwargs):
        """Unterdrückt das Per-Request-Logging (sonst flutet es das gemeinsame Log)."""


# DEF: Prozess-Einstiegspunkt
def main() -> None:
    """Startet den statischen Datei-Server (Lock-Datei, http.server)."""
    # MIME-Tabelle ergänzen, bevor der erste Request kommt (guess_type nutzt mimetypes).
    for ext, mime in _EXTRA_MIME.items():
        mimetypes.add_type(mime, ext)

    # PID in web.lock schreiben (für spotify-db --status / --stop)
    web_lock = get_web_lock_path()
    if web_lock:
        web_lock.parent.mkdir(parents=True, exist_ok=True)
        web_lock.write_text(str(os.getpid()))

    directory = get_public_dir()
    # Verzeichnis anlegen, falls es noch fehlt — sonst listet der Server nur einen 404.
    directory.mkdir(parents=True, exist_ok=True)

    host = get_web_host()
    port = get_web_port()

    # directory-kwarg bindet den Handler an das auszuliefernde Verzeichnis.
    handler = partial(_StaticHandler, directory=str(directory))
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            print(f"Fehler: Port {port} ist bereits belegt.")
        elif e.errno == errno.EADDRNOTAVAIL:
            print(f"Fehler: Adresse {host} ist auf diesem Rechner nicht verfügbar.")
        else:
            print(f"Fehler beim Start des Web-Servers: {e}")
        raise SystemExit(1) from e

    print(f"Serviere {directory}")
    print(f"Erreichbar auf http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
