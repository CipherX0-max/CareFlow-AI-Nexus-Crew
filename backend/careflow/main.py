"""CareFlow AI API. Run: uvicorn backend.careflow.main:app --port 8000"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_settings
from .db import get_engine, session_scope
from .models import Base
from .routers import admin, auth, patient, staff
from .services import pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("careflow")


def init_database(create_all_fallback: bool = True) -> None:
    """Apply migrations (Alembic). Falls back to create_all only when Alembic is unavailable (tests / first run)."""
    eng = get_engine()
    try:
        from alembic import command
        from alembic.config import Config
        from .config import PKG
        cfg = Config()
        cfg.set_main_option("script_location", str(PKG / "migrations"))
        cfg.set_main_option("sqlalchemy.url", str(eng.url.render_as_string(hide_password=False)).replace("%", "%%"))
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
    except Exception:  # noqa: BLE001
        if not create_all_fallback:
            raise
        log.exception("Alembic upgrade failed; falling back to create_all")
        Base.metadata.create_all(eng)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.validate_for_runtime()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    init_database()
    with session_scope() as db:
        pipeline.sync_ruleset(db)
        # a restart can strand cases mid-processing; mark them so a human sees them (fail-safe) and they can be reassessed
        db.execute(text("UPDATE cases SET processing_state='failed' WHERE processing_state='processing'"))
    yield


app = FastAPI(title="CareFlow AI", version="0.2.0", lifespan=lifespan,
              description="Local-first, human-in-the-loop intake and urgency-prioritisation MVP. Not a diagnostic system; demonstration rules only.")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_list, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Content-Type", "X-CSRF-Token"])


@app.middleware("http")
async def limits_and_headers(request: Request, call_next):
    s = get_settings()
    cl = request.headers.get("content-length")
    limit = (max(s.max_upload_mb, s.max_audio_mb) + 2) * 1024 * 1024
    if cl and cl.isdigit() and int(cl) > limit:
        return JSONResponse({"error": {"code": "payload_too_large", "message": "Request is too large."}}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.exception_handler(StarletteHTTPException)
async def http_exc(_: Request, exc: StarletteHTTPException):
    d = exc.detail
    body = {"error": d if isinstance(d, dict) and "code" in d else {"code": "http_error", "message": str(d)}}
    return JSONResponse(body, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exc(_: Request, exc: RequestValidationError):
    fields = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse({"error": {"code": "validation_error", "message": "Some fields are invalid.", "fields": fields}}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse({"error": {"code": "internal_error", "message": "Something went wrong on our side. Nothing was lost; please try again."}}, status_code=500)


@app.get("/api/health", tags=["health"])
def health():
    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503)


for r in (auth.router, patient.router, admin.router):
    app.include_router(r)
# one shared case-priority state, two workspaces; what each role may do is decided per capability in the router/deps, not by the URL prefix
app.include_router(staff.router, prefix="/api/staff")
app.include_router(staff.router, prefix="/api/doctor", include_in_schema=False)
