# Project Context: Compliance-Onboarding Agent Demo for Stablecoin Clearinghouse

## What this is

A 3-day prototype demonstrating a two-agent compliance verification pipeline for a stablecoin clearinghouse (modeled loosely on Better Money Company). Built to show technical depth after a company passed on a product-manager-style pitch, wanting to see production-quality code, not a design doc or Figma mockup.

**Scope decision (locked, per mentor guidance from Peter Braunz, Anchorage Digital):** Build the **compliance-onboarding agent flow** (x401 identity + x402 payment), NOT a netting/settlement engine. The netting/compression problem is well-understood prior art (40+ years, patented algorithms); compliance onboarding for agent-to-agent clearinghouse admission is the genuinely novel, differentiating angle.

**What "impressive" looks like for this demo:** two agents actually communicating, presenting credentials, getting admitted, and a transaction happening as a result, not just isolated compliance checks running in silos. Real backend logic that can be explained and defended line-by-line, not just a polished UI.

---

## Core architecture

Two structurally identical but separately-scoped verification agents:

### 1. Client KYB Verifier

Verifies that an **enterprise** (a business client, not a financial institution) is a legitimate business before admitting it to transact through the clearinghouse.

- **Input**: business documents (Secretary of State filing, regulatory licenses, articles of incorporation)
- **Output**: a signed Verifiable Credential (VC), e.g.:

json

```json
  {
    "entity": "Acme Trading LLC",
    "ein": "XX-XXXXXXX",
    "kyb_status": "passed",
    "verified_by": "MockKYBProvider",
    "signature": "<cryptographic sig>",
    "issued_at": "<timestamp>"
  }
```

- The document-parsing step (unstructured docs → structured claims) is the **one place an LLM is justified** in this pipeline, because document format is unpredictable. Once parsed into structured data, everything downstream is deterministic.

### 2. Issuer Compliance Verifier

Verifies that a **stablecoin issuer** (Circle/USDC, Paxos/PYUSD) meets GENIUS Act compliance criteria. This is entirely separate from client KYB — it checks the asset's legitimacy, not the client's.

- **Input**: issuer's public reserve disclosures (monthly/quarterly reports)
- **Output**: a compliance scorecard, e.g.:

json

```json
  {
    "issuer": "Circle",
    "us_domiciled": true,
    "reserve_ratio": 1.02,
    "audit_tier": "Big Four",
    "disclosure_frequency": "monthly",
    "compliant": true
  }
```

- Runs on a **cached, periodic schedule** (daily/weekly), not per-transaction — check frequency should never exceed the frequency at which the underlying source data actually updates.

**Since GENIUS Act implementing regulations aren't finalized yet (as of July 2026), the compliance rule set must be built directly from the statutory text itself**, not from a regulator API that doesn't exist yet. Rule set (derived from GENIUS Act Sec. 4, Sec. 5):

1. US domicile check (Sec. 2(23)) — excludes non-US-domiciled issuers (e.g. Tether/USDT)
2. Reserve composition check (Sec. 4(a)(1)(A)) — only cash, Fed deposits, insured deposits, ≤93-day Treasuries
3. Reserve ratio check — reserves ≥ 100% of outstanding tokens
4. Disclosure frequency check — public reserve report at least monthly (Sec. 4(a)(1)(C))
5. Auditor tier check — registered public accounting firm (Sec. 4(a)(3)(A)); optionally tier Big Four vs. non-Big Four as a confidence score
6. Executive certification check (Sec. 4(a)(3)(B))
7. Redemption policy check — public, clear, disclosed fees (Sec. 4(a)(1)(B))
8. No-interest check (Sec. 4(a)(11)) — issuer doesn't pay yield directly on the token
9. Sanctions/AML program check (Sec. 5(i))
10. No false-backing claims check (Sec. 4(e)) — no implied FDIC/government backing

**Design decision needed:** does a failed check hard-block the transaction, or downgrade confidence and require human sign-off? Recommendation: hard-block for this feature specifically, since a false approval on "compliant source of funds" is the worst possible failure mode to demo.

---

## Protocol layer: x401 + x402

**Common misconception to avoid:** x401 is identity, x402 is payment. They are not interchangeable and do different jobs.

- **x401** (launched by Proof, June 25, 2026 — very new, built with contributors from OpenAI, Okta, Circle, Lightspark, MATTR): an HTTP-native challenge-response protocol. Server returns a 401 response describing required identity proof; agent responds with a cryptographically signed Verifiable Credential from an identity wallet. Supports selective disclosure / zero-knowledge proofs. **Verifies who authorized the agent's action (legal attribution), not the agent itself.** Does NOT review raw documents — it transports and verifies possession of a credential that was already issued by a separate verifier (see KYB Verifier above).
- **x402** (Coinbase, May 2025): payment protocol for agents, settles primarily in USDC over HTTP, no accounts/API keys needed.

**Corrected pipeline:**

```
1. CAPTURE — Client uploads documents to UI
2. IDENTITY VERIFICATION (KYB Verifier, off-chain) — documents → signed VC
3. ADMISSION — Client's agent presents VC via x401 → Clearinghouse verifies → admits/denies
4. FUNDS MOVEMENT (x402) — once admitted, payment/settlement executes
```

Two separate, parallel verification tracks feed into admission:

- **Enterprise compliance check** (left side): Enterprise → documents → Client KYB Verifier → signed KYB credential → presented via x401 to Clearing House agent
- **Issuer compliance check** (right side): Clearing House agent queries Issuer Compliance Verifier → verifier requests reserve disclosure from issuer → issuer returns compliance status + scorecard

---

## Scope simplifications (state these explicitly in the README — do not let them look like oversights)

1. **Same-issuer settlement only for v1.** Cross-issuer conversion (USDC ↔ PYUSD) is a stated next-phase extension, not built in v1. Real-world clearinghouses handle this via a redemption-based swap (1 PYUSD = 1 USDC, no fee) that is NOT the same as a DEX swap — DEX pricing floats with liquidity; a redemption-based swap is an issuer-backed par guarantee.
2. **Treasury/internal-swap model, not live issuer redemption API integration.** Core Infra is assumed to hold pooled USDC/PYUSD reserves internally and nets/converts directly, rather than calling issuer redemption APIs per-transaction. This means issuers stay a **passive compliance-reference layer** — they are never told about individual client transactions or admissions.
3. **Netting is assumed batch, not real-time**, consistent with the original CMS proposal language ("collects... executes a single netted settlement").
4. **Multi-asset netting is out of scope** — obligations in different stablecoins are not netted against each other without an explicit peg-conversion assumption (this is the "multi-asset trap": USDC and USDT/PYUSD obligations cannot net directly).
5. **Netting/settlement/execution layers are mocked or omitted entirely** per the locked scope decision — this demo is about the compliance-onboarding agent flow, not CMS-style netting.
6. **x401 flow is simulated against the published spec**, not integrated with a live Proof SDK, since the protocol is brand new (shipped ~10 days before this build) and any SDK is likely still thin/shifting.

---

## Tech stack recommendation

- **Backend**: Python (FastAPI) or Node (Express) — no agent framework needed (explicitly avoid OpenClaw/Hermes-style personal-agent frameworks; they're built for ambient autonomy across a whole machine, wrong trust model for a financial compliance demo, and have known security issues, e.g. CVE-2026-25253).
- **LLM calls**: direct Anthropic API calls (you have API access) using tool use / structured output, scoped ONLY to the document-parsing step (unstructured KYB documents → structured claims). Do not use an LLM for the compliance rule evaluation itself — that's deterministic logic.
- **Ledger/ ledger-style state** (if simulating institution balances at all): Postgres with double-entry design, or in-memory dicts for a pure demo.
- **Dev tool**: Claude Code — used to help write the service faster, not as a runtime component of the product itself.
- **Hosting for demo**: Render, Railway, or [Fly.io](http://Fly.io) for a shareable URL, or run locally and screen-share if a live link isn't necessary.

---

## Prior work in this track (separate, not part of current 3-day scope, useful background)

A previous conversation thread (before this scope pivot) worked through a full CMS-style multilateral netting engine design in depth, including:

- Trade object schema: trade ID (unique at capture, before any on-chain hash exists), venue ID, counterparties, direction, asset, amount, timestamp (with disambiguation precision beyond day-level), lifecycle state field (captured → verified → netted → settled → confirmed), and an institution-level collateral/credit reference field.
- Synthetic data generation requiring deliberate skew (not uniform randomness) across venue concentration, counterparty concentration, and trade size distribution — needed specifically to surface concentration risk, human-in-the-loop threshold testing, and default risk, none of which appear in uniform synthetic data.
- Worked netting math: a 3-party zero-sum cycle (A owes B $10M, B owes C $10M, C owes A $10M → all net to zero, zero transfers needed) and a 4-party partial-netting case (A owes B $15M, B owes C $9M, C owes D $9M, D owes A $5M, B owes A $2M → net positions A: -$8M, B: +$4M, C: $0, D: +$4M → minimum 2 transfers: A→B $4M, A→D $4M), with C dropping out of settlement entirely despite having real gross activity ($9M in, $9M out).
- Metrics framework: correctness (balance/conservation checks), reduction efficiency (transaction count reduction, value-at-risk reduction), realism/stress-testing (concentration sensitivity, threshold breach detection), performance (lowest priority).

This background is not being built in the current 3-day sprint but may inform later phases once the compliance-onboarding demo is complete and the relationship with Better Money Company / Anchorage progresses.

---

## Key external references

- GENIUS Act full text (Public Law 119-27, signed July 18, 2025) — source of truth for the compliance rule set, since implementing regulations are not yet final.
- x401 protocol: [https://www.proof.com/blog/introducing-x401](https://www.proof.com/blog/introducing-x401)
- x402 protocol: Coinbase, May 2025 launch.
- Circle (USDC) transparency page: [tether.to](http://tether.to)-equivalent for Circle, weekly reserve holdings + monthly Deloitte attestation.
- Paxos (PYUSD) — KPMG-audited monthly attestations since Feb 2025.

