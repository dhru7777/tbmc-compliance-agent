# TBMC's Compliance Agent

Enterprise KYB onboarding and stablecoin issuer compliance demo for [The Better Money Company](https://bettermoney.com).

## Stack

- **Frontend** — static HTML/CSS/JS (`frontend/`, port 5173)
- **Backend** — FastAPI (`backend/`, port 8000)

## Quick start

```bash
# Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add ANTHROPIC_API_KEY
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
python3 -m http.server 5173
```

Open http://127.0.0.1:5173

## Flow

Document upload → VERIFY (AI + deterministic rules) → scorecard → **Stage 3 x401 credential** (if passed)

```
Stage 1: Document upload → AI parsing → public presence check
Stage 2: Deterministic cross-reference → confidence score → passed / flagged / blocked
Stage 3: If passed → signed x401 compliance credential + PDF certificate
```

## x401 (simulated)

**x401 flow is simulated per the published protocol spec, not integrated with the live Proof SDK**, given the protocol's newness as of this build.

This prototype implements **issuer-side credential issuance** (clearinghouse signs after KYB passes). It does **not** implement the HTTP `PROOF-REQUEST` / `PROOF-RESPONSE` admission handshake.

**Input-side identity verification** (authenticating who is allowed to submit a KYB request before Agent 1 processes documents) is **out of scope** for this prototype. x401 is implemented at the output/credential-issuance stage only.

- Signed JSON credential stored under `backend/records/kyb/{session_id}/`
- PDF certificate for UI display
- Public key: `GET /.well-known/tbmc-signing-key.json`
- Verify API: `GET /api/enterprise/kyb/{session_id}/credential`

Generate production signing keys:

```bash
cd backend && python scripts/generate_signing_key.py
```

## Backend layout (Stage 3)

```
backend/app/services/x401/
  signing.py          # Ed25519 keypair, sign, verify
  credential.py       # issue_compliance_credential, credit limit, criteria map
  certificate_pdf.py  # minimal PDF certificate
  __init__.py
backend/app/services/x401_service.py   # facade
backend/app/services/credential_store.py
backend/app/routers/well_known.py
backend/scripts/generate_signing_key.py
```

See `agent-skill/Claude_context.md` for product scope and architecture notes.

## Deploy (Railway backend)

This repo is a **monorepo** (`frontend/` + `backend/`). Railway must build the API from `backend/`.

**Option A — recommended:** In Railway → service **Settings** → set **Root Directory** to `backend`. Point config file to `/backend/railway.toml`.

**Option B — build from repo root:** Keep root `railway.toml` (uses `backend/Dockerfile`). No root directory change needed.

Set `ANTHROPIC_API_KEY` in Railway variables. Health check: `GET /api/health`.
