import os

from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.database import init_db, is_db_enabled, ping_db
from app.routers import enterprise, issuer


@asynccontextmanager
async def lifespan(app: FastAPI):
    if is_db_enabled():
        ok = init_db()
        if ok:
            print("PostgreSQL connected — kyb_verifications table ready")
        else:
            print("WARNING: DATABASE_URL set but Postgres init failed")
    else:
        print("DATABASE_URL not set — verifications will not persist to Postgres")
    yield


app = FastAPI(
    title="TBMC Compliance Onboarding Demo",
    description="Client KYB + Issuer compliance verification for clearinghouse admission",
    version="0.2.0",
    lifespan=lifespan,
)

_default_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
]
_extra_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins + _extra_origins,
    allow_origin_regex=os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.netlify\.app"),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(enterprise.router, prefix="/api/enterprise", tags=["enterprise"])
app.include_router(issuer.router, prefix="/api/issuer", tags=["issuer"])


@app.get("/api/health")
def health():
    db_ok = False
    if is_db_enabled():
        try:
            db_ok = ping_db()
        except Exception:
            db_ok = False
    return {
        "status": "ok",
        "database": "connected" if db_ok else ("disabled" if not is_db_enabled() else "error"),
    }
