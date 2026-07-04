from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import enterprise, issuer

app = FastAPI(
    title="TBMC Compliance Onboarding Demo",
    description="Client KYB + Issuer compliance verification for clearinghouse admission",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(enterprise.router, prefix="/api/enterprise", tags=["enterprise"])
app.include_router(issuer.router, prefix="/api/issuer", tags=["issuer"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
