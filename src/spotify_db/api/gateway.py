"""App-weites Gateway der HTTP-API: API-Key-Auth + CORS.

Die Auth ist DB-basiert (Tabelle `api_keys`, siehe `spotify_db.db.keys`): pro
Aufrufer ein eigener, einzeln widerrufbarer Key. Widerruf greift sofort, weil
pro Request in der DB nachgeschlagen wird — kein Cache, kein Neustart nötig.

Fail-closed-Semantik (wie die alte Env-Key-Auth):
  * keine aktiven Keys angelegt  → 503 auf alles (Server-Fehlkonfiguration)
  * fehlender/falscher/widerrufener Key → 401
  * OPTIONS bleibt frei (CORS-Preflight schickt keine Auth-Header)
  * /docs + /openapi.json bleiben frei (Auto-Docs ohne Key bedienbar; LAN-only,
    Single-User, alle Daten-Endpoints bleiben key-geschützt)

CORS setzt die App selbst (kein Apache mehr davor): `CORSMiddleware` legt die
Access-Control-Header gemäß `cors_origins` aus der config.json an (Default "*",
vertretbar weil jede Antwort key-geschützt ist). Weil die Middleware außen liegt,
trägt auch jede 401/503-Antwort der Auth die CORS-Header.

Ein späterer Rate-Limiter würde hier einhängen: als zweite Middleware direkt
hinter der Auth.
"""

from starlette.concurrency import run_in_threadpool
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from spotify_db.common.config import load_config
from spotify_db.db import keys

# CONFIG: Pfade, die ohne API-Key erreichbar bleiben (Auto-Docs + OpenAPI-Schema).
_AUTH_EXEMPT_PATHS = {"/docs", "/openapi.json", "/docs/oauth2-redirect"}


# DEF: Key aus dem Request lesen
def _presented_key(request: Request) -> str:
    """Liest den Key aus 'Authorization: Bearer <key>' oder 'X-API-Key'."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


# DEF: Gateway an der App registrieren
def init_gateway(app) -> None:
    """Registriert Auth (HTTP-Middleware) und CORS (CORSMiddleware) an der FastAPI-App.

    Reihenfolge ist relevant: Starlette stapelt die zuletzt hinzugefügte Middleware
    nach außen. CORS wird daher als Letztes hinzugefügt — so handhabt sie den
    OPTIONS-Preflight und legt CORS-Header auch über die Auth-Fehlerantworten.

    Args:
        app: Die FastAPI-App, die geschützt werden soll.
    """

    # MARK: - Auth (HTTP-Middleware, ersetzt das frühere before_request) -
    @app.middleware("http")
    async def _require_api_key(request: Request, call_next):
        """Schützt alle Endpunkte per API-Key aus der Datenbank."""
        if request.method == "OPTIONS" or request.url.path in _AUTH_EXEMPT_PATHS:
            return await call_next(request)
        # Happy Path zuerst (eine einzige DB-Abfrage); nur im Fehlerfall wird
        # zusätzlich unterschieden, ob überhaupt Keys existieren (503 vs. 401).
        # Die DB-Zugriffe sind blockierend → in den Threadpool auslagern, damit
        # der Event-Loop frei bleibt.
        presented = _presented_key(request)
        if presented and await run_in_threadpool(keys.verify_key, presented):
            return await call_next(request)
        if not await run_in_threadpool(keys.has_active_keys):
            return JSONResponse(
                {"error": "Server misconfigured: keine aktiven API-Keys angelegt"},
                status_code=503,
            )
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # MARK: - CORS (zuletzt hinzugefügt = äußerste Middleware) -
    origins = load_config().get("cors_origins", ["*"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type"],
        max_age=3600,
    )
