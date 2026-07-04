# KYB Test Sets

Use these to demo LLM search + cross-check.

## Test Set A — Clean match (expect mostly PASS)

| Field | Value |
|-------|-------|
| Legal name | Acme Trading LLC |
| State | DE |
| Operating address | 1209 Orange St, Wilmington, DE 19801 |
| Business purpose | Commodity contracts dealing |

## Test Set B — Mismatch demo (expect FLAGs)

| Field | Value |
|-------|-------|
| Legal name | Stripe Inc |
| State | DE |
| Operating address | 123 Fake Street, Miami, FL 33101 |
| Business purpose | Cryptocurrency exchange and stablecoin issuance |

Upload any PDF with descriptive labels (e.g. "Secretary of State filing", "Government ID").
