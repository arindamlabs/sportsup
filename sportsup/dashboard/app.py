"""FastAPI app for the admin dashboard: routes, Basic auth, and the uvicorn runner.

Auth is HTTP Basic (constant-time compare) as defense-in-depth; the real protection is
that the server binds to localhost and is reached over an SSH tunnel. All routes are
read-only. The app holds shared SubscriberStore/StateStore handles and issues SELECTs.
"""

from __future__ import annotations

import logging
import secrets as pysecrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ..state import StateStore
from ..subscribers import SubscriberStore
from . import data
from .render import render_page

logger = logging.getLogger("sportsup.dashboard")


def create_app(sub_store: SubscriberStore, state: StateStore, *, user: str, password: str) -> FastAPI:
    app = FastAPI(title="SportsUp Admin", docs_url=None, redoc_url=None, openapi_url=None)
    security = HTTPBasic()

    def require_auth(creds: HTTPBasicCredentials = Depends(security)) -> bool:
        ok_user = pysecrets.compare_digest(creds.username, user)
        ok_pass = pysecrets.compare_digest(creds.password, password)
        if not (ok_user and ok_pass):
            raise HTTPException(status_code=401, detail="Unauthorized",
                                headers={"WWW-Authenticate": "Basic"})
        return True

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def index(_: bool = Depends(require_auth)) -> str:
        return render_page(
            data.build_overview(sub_store, state),
            data.build_user_rows(sub_store),
            data.build_popularity(sub_store),
        )

    @app.get("/api/overview")
    def api_overview(_: bool = Depends(require_auth)) -> dict:
        return data.overview_json(sub_store, state)

    @app.get("/api/subscribers")
    def api_subscribers(_: bool = Depends(require_auth)) -> list[dict]:
        return data.subscribers_json(sub_store)

    return app


def run_dashboard(db_path: str, host: str, port: int, *, user: str, password: str) -> None:
    """Build the app over the state DB and serve it (blocking)."""
    import uvicorn

    store = StateStore(db_path)
    app = create_app(SubscriberStore(store), store, user=user, password=password)
    logger.info("dashboard listening on http://%s:%d (Basic auth)", host, port)
    uvicorn.run(app, host=host, port=port, log_level="warning")
