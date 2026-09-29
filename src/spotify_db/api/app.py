"""Lokale HTTP-API auf Port 15001 — Kompositions-Wurzel des API-Prozesses.

Hier werden nur die Bausteine zusammengesetzt: die Router aus `routes/`
(dünne Übersetzungsschicht zu den Domänen), das Gateway (Auth + CORS) und
der Discovery-Endpoint `/`. Logik gehört in die Domänen (`spotify_db.db`,
`spotify_db.spotify`), nicht hierher.

Die Routen sind ein stabiler Vertrag: interne Umbauten dürfen die nach außen
sichtbaren Pfade nicht ändern. Die API ist lokal und im Heimnetz erreichbar (bindet auf 0.0.0.0:15001).

FastAPI/uvicorn (ASGI), als **einzelner** Prozess (workers=1) — Tracker und API
teilen sich eine SQLite-Connection mit Lock und je eine PID-Lock-Datei; mehrere
Worker würden dieses Modell brechen.

Start: python -m spotify_db.api.app  (oder via `spotify-db --run --api`)
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from spotify_db.api.gateway import init_gateway
from spotify_db.api.routes.db import router as db_router
from spotify_db.api.routes.live import router as live_router
from spotify_db.api.routes.playback import router as control_router
from spotify_db.api.routes.songs import router as songs_router
from spotify_db.common.config import get_api_lock_path
from spotify_db.db.database import close_connection, initialize_db
from spotify_db.db.localdb import close_local_connection, initialize_local_db
from spotify_db.db.songsdb import close_songs_connection


# DEF: Lifespan (Schema-Init beim Start, Connections schließen beim Shutdown)
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialisiert das DB-Schema beim Start und schließt die Connections beim Shutdown.

    Ersetzt das frühere atexit/SIGTERM-Handling: uvicorn fährt bei SIGTERM sauber
    herunter und löst dadurch den Shutdown-Teil (nach dem `yield`) aus.
    """
    # STATE: Schema sicherstellen, falls die API ohne Tracker gestartet wird
    initialize_db()
    initialize_local_db()
    yield
    close_connection()
    close_local_connection()
    close_songs_connection()


# DEF: App-Fabrik
def create_app() -> FastAPI:
    """Baut die FastAPI-App: Router, Gateway (Auth + CORS), Discovery-Endpoint.

    Returns:
        FastAPI: Die fertig konfigurierte App.
    """
    app = FastAPI(
        title="spotify-db API",
        description="Lokale HTTP-API für den Spotify-Hörverlauf-Tracker.",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.include_router(live_router)
    app.include_router(db_router)
    app.include_router(control_router)
    app.include_router(songs_router)
    init_gateway(app)

    # SECTION: - Discovery -

    # DEF: Discovery-Endpoint (Übersicht)
    @app.get("/")
    def list_endpoints():
        """Übersicht aller verfügbaren API-Endpunkte (baut sich aus dem OpenAPI-Schema auf)."""
        # Das OpenAPI-Schema ist der stabile, versionsfeste Weg, alle Routen aufzuzählen
        # (es enthält genau die ins Schema aufgenommenen Endpunkte, also ohne /docs +
        # /openapi.json). FastAPI legt die erste Docstring-Zeile als `description` ab.
        endpoints = []
        for path, operations in app.openapi().get("paths", {}).items():
            methods = sorted(m.upper() for m in operations if m.upper() not in ("HEAD", "OPTIONS"))
            first_op = next(iter(operations.values()), {})
            text = first_op.get("description") or first_op.get("summary") or ""
            doc = text.strip().splitlines()[0] if text.strip() else ""
            endpoints.append({"path": path, "methods": methods, "description": doc})
        endpoints.sort(key=lambda e: e["path"])
        return {"endpoints": endpoints, "count": len(endpoints)}

    # SECTION: - OpenAPI: API-Key-Security nur für die Doku -

    # DEF: OpenAPI-Schema um die API-Key-Schemata anreichern
    def _custom_openapi() -> dict:
        """Ergänzt das OpenAPI-Schema um die API-Key-Schemata (Authorize-Button in /docs).

        Rein dokumentierend: die Durchsetzung der Auth bleibt in der Gateway-Middleware.
        Dieses Schema sorgt nur dafür, dass die Swagger-UI einen „Authorize"-Button zeigt
        und „Try it out"-Requests den Key mitschicken.
        """
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "BearerAuth": {"type": "http", "scheme": "bearer"},
        }
        # OR-Semantik: einer der beiden Header genügt (wie in der Middleware).
        schema["security"] = [{"ApiKeyHeader": []}, {"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi

    return app


# DEF: Prozess-Einstiegspunkt
def main() -> None:
    """Startet den API-Server (Lock-Datei, uvicorn)."""
    # PID in api.lock schreiben (für spotify-db --status / --stop)
    api_lock = get_api_lock_path()
    if api_lock:
        api_lock.parent.mkdir(parents=True, exist_ok=True)
        api_lock.write_text(str(os.getpid()))

    # 0.0.0.0: lokal und im Heimnetz erreichbar. Einzelner Prozess (kein workers),
    # access_log aus (ersetzt die frühere werkzeug-Drosselung auf ERROR).
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=15001,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
