import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from telethon import errors

from . import api, auth, db, panel
from .config import APP_NAME, VERSION
from .telegram import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.prune_log()
    await manager.start_all()
    yield
    await manager.stop_all()


app = FastAPI(
    title=APP_NAME, version=VERSION, lifespan=lifespan,
    description="Your own Telegram account as an HTTP API. Every MTProto method is available via `POST /v1/invoke`.",
    docs_url="/swagger", redoc_url="/redoc", openapi_url="/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _err(status: int, code: str, message: str, **extra):
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return _err(exc.status_code, {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}
                .get(exc.status_code, "bad_request" if exc.status_code < 500 else "unavailable"), str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return _err(422, "validation_error", "invalid request", details=exc.errors())


@app.exception_handler(errors.FloodWaitError)
async def flood(request: Request, exc: errors.FloodWaitError):
    return JSONResponse({"error": {"code": "FLOOD_WAIT", "message": str(exc), "retry_after": exc.seconds}},
                        status_code=429, headers={"Retry-After": str(exc.seconds)})


@app.exception_handler(errors.RPCError)
async def rpc_error(request: Request, exc: errors.RPCError):
    return _err(400 if (exc.code or 400) < 500 else 502, exc.message or type(exc).__name__, str(exc),
                telegram_code=exc.code)


@app.exception_handler(ValueError)
async def value_error(request: Request, exc: ValueError):
    return _err(400, "bad_request", str(exc))


@app.middleware("http")
async def log_and_harden(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/v1/"):
        p = getattr(request.state, "principal", None)
        if p:
            db.ex("INSERT INTO request_log(user_id, key_id, method, path, status, ms, ts) VALUES(?,?,?,?,?,?,?)",
                  (p.user_id, p.key_id, request.method, request.url.path[:200], response.status_code,
                   int((time.perf_counter() - t0) * 1000), db.now()))
    else:
        response.headers.setdefault("X-Frame-Options", "DENY")
    return response


app.include_router(auth.router)
app.include_router(panel.router)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True, "version": VERSION, "accounts_online": len(manager.clients)}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")
