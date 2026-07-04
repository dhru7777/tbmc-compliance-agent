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

Document upload → VERIFY (AI + deterministic rules) → scorecard (x401 credential deferred)

See `agent-skill/Claude_context.md` for product scope and architecture notes.
