# Compliance Onboarding Agent — Frontend

Plain HTML/CSS/JS UI styled to match [The Better Money Company](https://bettermoney.com/).

## Pages / flow

1. **Landing** — TBMC-style hero + two path cards (Business vs Issuer)
2. **Enterprise KYB** — company name, EIN, add documents one at a time
3. **Issuer compliance** — issuer name, ticker (USDC/PYUSD), add documents one at a time

## Run

Serve the folder with any static server (backend must be running on port 8000):

```bash
cd frontend
python -m http.server 5173
```

Open http://localhost:5173
