# Compliance Onboarding Agent — Backend

FastAPI service for the TBMC-style compliance onboarding demo.

## Agents

- **Client KYB Verifier** — enterprise documents → signed Verifiable Credential
- **Issuer Compliance Verifier** — reserve disclosures → GENIUS Act scorecard

## Run locally

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs
