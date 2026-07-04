import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.services import kyb_service

router = APIRouter()


class KybSearchRequest(BaseModel):
    legal_name: str
    state: str = ""
    operating_address: str = ""
    business_purpose: str = ""


class KybStartRequest(BaseModel):
    legal_name: str
    state: str
    operating_address: str = ""
    business_purpose: str = ""


class KybConfirmRequest(BaseModel):
    confirmed: bool = True


class KybCrossCheckRequest(BaseModel):
    operating_address: str = ""
    business_purpose: str = ""


@router.post("/kyb/session")
async def kyb_create_session():
    """Create empty session — user details first, search runs as they type."""
    return await kyb_service.create_session()


@router.post("/kyb/{session_id}/search")
async def kyb_search(session_id: str, body: KybSearchRequest):
    """Debounced public record lookup while user fills in details."""
    try:
        return await kyb_service.refresh_public_search(
            session_id,
            body.legal_name,
            body.state,
            body.operating_address,
            body.business_purpose,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/kyb/start")
async def kyb_start(body: KybStartRequest):
    """Real-time LLM web search when company name + state are provided."""
    return await kyb_service.start_kyb(
        body.legal_name,
        body.state,
        body.operating_address,
        body.business_purpose,
    )


@router.post("/kyb/{session_id}/confirm")
async def kyb_confirm(session_id: str, body: KybConfirmRequest):
    try:
        return await kyb_service.confirm_entity(session_id, body.confirmed)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/kyb/{session_id}/cross-check")
async def kyb_cross_check(session_id: str, body: KybCrossCheckRequest):
    """Preview deterministic cross-check before final submit."""
    try:
        return await kyb_service.cross_check_preview(
            session_id, body.operating_address, body.business_purpose
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/checklist")
def kyb_checklist_template():
    from app.services import kyb_rules

    return {"items": kyb_rules.get_checklist_template()}


@router.get("/kyb/{session_id}")
def kyb_get_session(session_id: str):
    try:
        return kyb_service.get_session_summary(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/kyb/{session_id}/verify")
async def kyb_verify(
    session_id: str,
    legal_name: str = Form(default=""),
    state: str = Form(default=""),
    ein: str = Form(default=""),
    operating_address: str = Form(default=""),
    business_purpose: str = Form(default=""),
    beneficial_owners: str = Form(default="[]"),
    control_persons: str = Form(default="[]"),
    documents: list[UploadFile] = File(default=[]),
    document_labels: list[str] = Form(default=[]),
):
    """VERIFY only — AI doc parse + public presence + deterministic cross-ref (no x401 credential)."""
    try:
        owners = json.loads(beneficial_owners) if beneficial_owners else []
        persons = json.loads(control_persons) if control_persons else []
        uploads = []
        for i, doc in enumerate(documents):
            label = document_labels[i] if i < len(document_labels) else doc.filename or f"document_{i}"
            content = await doc.read()
            uploads.append((label, doc.filename or "upload", content))
        return await kyb_service.run_verify_only(
            session_id, uploads, legal_name, state, ein, operating_address, business_purpose, owners, persons
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in owners/persons fields")


@router.post("/kyb/{session_id}/submit")
async def kyb_submit(
    session_id: str,
    legal_name: str = Form(default=""),
    state: str = Form(default=""),
    ein: str = Form(default=""),
    operating_address: str = Form(default=""),
    business_purpose: str = Form(default=""),
    beneficial_owners: str = Form(default="[]"),
    control_persons: str = Form(default="[]"),
    documents: list[UploadFile] = File(default=[]),
    document_labels: list[str] = Form(default=[]),
):
    """Extract doc claims via LLM, cross-reference with deterministic rules."""
    try:
        owners = json.loads(beneficial_owners) if beneficial_owners else []
        persons = json.loads(control_persons) if control_persons else []
        uploads = []
        for i, doc in enumerate(documents):
            label = document_labels[i] if i < len(document_labels) else doc.filename or f"document_{i}"
            content = await doc.read()
            uploads.append((label, doc.filename or "upload", content))
        return await kyb_service.submit_kyb(
            session_id, ein, operating_address, business_purpose, owners, persons, uploads,
            legal_name, state,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in owners/persons fields")


@router.get("/kyb/{session_id}/credential")
def kyb_credential(session_id: str):
    try:
        cred = kyb_service.get_credential(session_id)
        if not cred:
            raise HTTPException(status_code=404, detail="Credential not issued yet")
        return {"session_id": session_id, "credential": cred}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/{session_id}/record")
def kyb_record(session_id: str):
    try:
        content = kyb_service.get_record(session_id)
        return {"session_id": session_id, "markdown": content}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


# Legacy stub kept for backwards compatibility
@router.post("/submit")
async def submit_enterprise_kyb(
    company_name: str = Form(...),
    ein: str = Form(default=""),
    documents: list[UploadFile] = File(default=[]),
):
    return {
        "status": "deprecated",
        "message": "Use POST /api/enterprise/kyb/start for the new KYB flow",
        "company_name": company_name,
        "ein": ein,
        "document_count": len(documents),
    }
