from fastapi import APIRouter, File, Form, UploadFile

router = APIRouter()

SUPPORTED_ISSUERS = ["USDC", "PYUSD", "USDG", "RLUSD", "frxUSD", "Other"]


@router.get("/supported")
def list_supported_issuers():
    return {"issuers": SUPPORTED_ISSUERS}


@router.post("/submit")
async def submit_issuer_compliance(
    issuer_name: str = Form(...),
    stablecoin_ticker: str = Form(...),
    documents: list[UploadFile] = File(default=[]),
):
    """Accept issuer reserve disclosures; GENIUS rule evaluation wired in next step."""
    return {
        "status": "received",
        "issuer_name": issuer_name,
        "stablecoin_ticker": stablecoin_ticker,
        "document_count": len(documents),
        "document_names": [d.filename for d in documents],
        "compliant": None,
    }
