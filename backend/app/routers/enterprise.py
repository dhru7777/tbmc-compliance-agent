import asyncio
import json

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from app.services import demo_companies, kyb_service, session_store

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


class KybChatRequest(BaseModel):
    message: str = ""


@router.post("/kyb/session")
async def kyb_create_session():
    """Create empty session — verification runs on submit via agent orchestrator."""
    return await kyb_service.create_session()


@router.post("/cache/clear")
def clear_api_cache():
    """Clear disk + in-process LLM/search response cache."""
    from app.services import api_cache

    removed = api_cache.clear_all()
    return {"cleared": removed, "cache_enabled": api_cache.CACHE_ENABLED}


@router.get("/demo-companies")
def list_demo_companies():
    """Trial company packages for UI dropdown."""
    return {"companies": demo_companies.list_demo_companies()}


@router.get("/demo-companies/{company_id}")
def get_demo_company_profile(company_id: str):
    try:
        return demo_companies.demo_profile(company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Demo company not found")


@router.get("/demo-companies/{company_id}/document.pdf")
def get_demo_company_pdf(company_id: str):
    try:
        pdf_bytes, filename, instance_id = demo_companies.build_demo_pdf(company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Demo company not found")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Demo-Document-Id": instance_id,
        },
    )


@router.get("/demo-companies/{company_id}/documents")
def get_demo_company_documents(company_id: str):
    """Return all mock-package text files for a trial company."""
    try:
        documents = demo_companies.load_mock_bundle_documents(company_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Demo company not found")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "company_id": company_id,
        "document_count": len(documents),
        "documents": documents,
    }


@router.post("/kyb/{session_id}/extract-documents")
async def kyb_extract_documents(
    session_id: str,
    trial_company_id: str = Form(default=""),
    documents: list[UploadFile] = File(default=[]),
    document_labels: list[str] = Form(default=[]),
):
    """Extract KYB fields from uploaded documents and return suggested form values."""
    if not documents:
        raise HTTPException(status_code=400, detail="Upload at least one document to extract.")
    try:
        uploads = []
        for i, doc in enumerate(documents):
            label = document_labels[i] if i < len(document_labels) else doc.filename or f"document_{i}"
            content = await doc.read()
            uploads.append((label, doc.filename or "upload", content))
        return await kyb_service.extract_documents_preview(
            session_id, uploads, trial_company_id=trial_company_id or None
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


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


@router.get("/kyb/verifications")
def kyb_list_verifications(limit: int = 50):
    """List recent persisted KYB verification records (requires DATABASE_URL)."""
    from app.services.verification_store import list_verifications

    rows = list_verifications(limit=min(limit, 200))
    return {"count": len(rows), "verifications": rows}


@router.get("/kyb/verifications/{enterprise_id}")
def kyb_get_verification(enterprise_id: str):
    """Fetch a persisted verification by enterprise_id (UUID)."""
    from app.services.verification_store import get_verification_by_id

    record = get_verification_by_id(enterprise_id)
    if not record:
        raise HTTPException(status_code=404, detail="Verification record not found")
    return record


@router.get("/kyb/{session_id}")
def kyb_get_session(session_id: str):
    try:
        return kyb_service.get_session_summary(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/kyb/{session_id}/chat")
async def kyb_chat(session_id: str, body: KybChatRequest):
    """Human chat bar — agent guides until KYB objective is achieved."""
    try:
        return await kyb_service.post_chat_message(session_id, body.message)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/{session_id}/chat")
def kyb_get_chat(session_id: str):
    try:
        session = kyb_service.get_session_summary(session_id)
        return {
            "session_id": session_id,
            "chat_messages": session.get("chat_messages") or [],
            "objective_status": session.get("objective_status"),
            "coach_last_message": session.get("coach_last_message", ""),
        }
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
    monthly_volume_low_usd: str = Form(default=""),
    monthly_volume_high_usd: str = Form(default=""),
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
        _require_documents(session_id, uploads)
        return await kyb_service.run_verify_only(
            session_id, uploads, legal_name, state, ein, operating_address, business_purpose, owners, persons
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in owners/persons fields")


async def _parse_kyb_submit_form(
    beneficial_owners: str,
    control_persons: str,
    documents: list[UploadFile],
    document_labels: list[str],
) -> tuple[list, list, list]:
    owners = json.loads(beneficial_owners) if beneficial_owners else []
    persons = json.loads(control_persons) if control_persons else []
    uploads = []
    for i, doc in enumerate(documents):
        label = document_labels[i] if i < len(document_labels) else doc.filename or f"document_{i}"
        content = await doc.read()
        uploads.append((label, doc.filename or "upload", content))
    return owners, persons, uploads


def _require_documents(session_id: str, uploads: list) -> None:
    if uploads:
        return
    if session_store.load_stored_uploads(session_id):
        return
    raise HTTPException(
        status_code=400,
        detail="At least one document is required. Upload formation or SOS files before running verification.",
    )


def _parse_volume(low: str, high: str) -> tuple[float | None, float | None]:
    """Parse monthly volume range from form strings."""
    try:
        low_val = float(low) if low.strip() else None
        high_val = float(high) if high.strip() else None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid monthly volume — use numbers only") from exc
    if low_val is not None and low_val < 0:
        raise HTTPException(status_code=400, detail="Monthly volume low must be >= 0")
    if high_val is not None and high_val < 0:
        raise HTTPException(status_code=400, detail="Monthly volume high must be >= 0")
    if low_val is not None and high_val is not None and high_val < low_val:
        raise HTTPException(status_code=400, detail="Monthly volume high must be >= low")
    return low_val, high_val


@router.post("/kyb/{session_id}/submit/stream")
async def kyb_submit_stream(
    session_id: str,
    legal_name: str = Form(default=""),
    state: str = Form(default=""),
    ein: str = Form(default=""),
    operating_address: str = Form(default=""),
    business_purpose: str = Form(default=""),
    monthly_volume_low_usd: str = Form(default=""),
    monthly_volume_high_usd: str = Form(default=""),
    beneficial_owners: str = Form(default="[]"),
    control_persons: str = Form(default="[]"),
    trial_company_id: str = Form(default=""),
    documents: list[UploadFile] = File(default=[]),
    document_labels: list[str] = Form(default=[]),
):
    """SSE stream of agent think/act/observe steps, then final scorecard JSON."""
    try:
        owners, persons, uploads = await _parse_kyb_submit_form(
            beneficial_owners, control_persons, documents, document_labels
        )
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in owners/persons fields")

    _require_documents(session_id, uploads)
    vol_low, vol_high = _parse_volume(monthly_volume_low_usd, monthly_volume_high_usd)

    queue: asyncio.Queue = asyncio.Queue()

    async def on_step(step: dict) -> None:
        await queue.put(step)

    async def run_submit() -> dict:
        try:
            return await kyb_service.submit_kyb(
                session_id,
                ein,
                operating_address,
                business_purpose,
                owners,
                persons,
                uploads,
                legal_name,
                state,
                monthly_volume_low_usd=vol_low,
                monthly_volume_high_usd=vol_high,
                trial_company_id=trial_company_id or None,
                on_step=on_step,
            )
        finally:
            await queue.put(None)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'ping', 'message': 'connected'})}\n\n"
        task = asyncio.create_task(run_submit())
        while True:
            try:
                step = await asyncio.wait_for(queue.get(), timeout=12.0)
            except asyncio.TimeoutError:
                if task.done():
                    break
                yield f"data: {json.dumps({'type': 'ping', 'message': 'processing'})}\n\n"
                continue
            if step is None:
                break
            yield f"data: {json.dumps(step)}\n\n"
            await asyncio.sleep(0.04)
        try:
            result = await task
            yield f"data: {json.dumps({'type': 'complete', **result})}\n\n"
        except KeyError:
            yield f'data: {json.dumps({"type": "error", "message": "Session not found"})}\n\n'
        except Exception as exc:
            yield f'data: {json.dumps({"type": "error", "message": str(exc)})}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/kyb/{session_id}/submit")
async def kyb_submit(
    session_id: str,
    legal_name: str = Form(default=""),
    state: str = Form(default=""),
    ein: str = Form(default=""),
    operating_address: str = Form(default=""),
    business_purpose: str = Form(default=""),
    monthly_volume_low_usd: str = Form(default=""),
    monthly_volume_high_usd: str = Form(default=""),
    beneficial_owners: str = Form(default="[]"),
    control_persons: str = Form(default="[]"),
    trial_company_id: str = Form(default=""),
    documents: list[UploadFile] = File(default=[]),
    document_labels: list[str] = Form(default=[]),
):
    """Agentic verify — doc extract, planner ReAct loop, deterministic scorecard."""
    try:
        owners, persons, uploads = await _parse_kyb_submit_form(
            beneficial_owners, control_persons, documents, document_labels
        )
        _require_documents(session_id, uploads)
        vol_low, vol_high = _parse_volume(monthly_volume_low_usd, monthly_volume_high_usd)
        return await kyb_service.submit_kyb(
            session_id, ein, operating_address, business_purpose, owners, persons, uploads,
            legal_name, state,
            monthly_volume_low_usd=vol_low,
            monthly_volume_high_usd=vol_high,
            trial_company_id=trial_company_id or None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in owners/persons fields")


@router.get("/document-catalog")
def document_catalog():
    """Canonical 10-document list with KYC/KYB and public/private classification."""
    from app.services.verification_credentials import list_document_catalog

    return {"documents": list_document_catalog()}


@router.get("/kyb/{session_id}/audit.md")
def kyb_audit_md(session_id: str):
    """Agent audit record: session trail + LLM calls + agent trace."""
    try:
        content = kyb_service.get_audit_md(session_id)
        if not content:
            raise HTTPException(status_code=404, detail="Audit record not found")
        return Response(
            content=content,
            media_type="text/markdown; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/{session_id}/kya-credential")
def kyb_kya_credential(session_id: str):
    """KYA proof = agent_id + session_id + audit.md-bound signed credential."""
    try:
        from app.services.kya_credential import verify_audit_binding, verify_kya_proof

        proof = kyb_service.get_kya_proof(session_id)
        if not proof:
            raise HTTPException(status_code=404, detail="KYA credential not issued yet")
        sig = verify_kya_proof(proof)
        sig["audit_md_binding_valid"] = verify_audit_binding(session_id, proof)
        return {
            "session_id": session_id,
            "kya_proof": proof,
            "verification": sig,
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/{session_id}/verification-credentials")
def kyb_verification_credentials(session_id: str):
    """Layered C1–C4 signed credentials issued after verification pass."""
    try:
        from app.services.verification_credentials import verify_layered_credentials

        bundle = kyb_service.get_layered_credentials(session_id)
        if not bundle:
            raise HTTPException(status_code=404, detail="Verification credentials not issued yet")
        return {
            "session_id": session_id,
            "enterprise_id": bundle.get("enterprise_id"),
            "layered_credentials": bundle,
            "signature_valid": verify_layered_credentials(bundle),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/{session_id}/credential")
def kyb_credential(session_id: str):
    try:
        cred = kyb_service.get_credential(session_id)
        if not cred:
            raise HTTPException(status_code=404, detail="Credential not issued yet")
        from app.services.x401_service import verify_credential

        return {
            "session_id": session_id,
            "credential": cred,
            "signature_valid": verify_credential(cred),
        }
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


_CERT_FILENAMES = {
    "compliance": "tbmc-compliance-certificate",
    "kyc": "tbmc-kyc-credential",
    "kyb": "tbmc-kyb-credential",
    "kya": "tbmc-kya-agent-proof",
}


def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


@router.get("/kyb/{session_id}/credentials/{kind}.pdf")
def kyb_certificate_by_kind(session_id: str, kind: str):
    if kind not in _CERT_FILENAMES:
        raise HTTPException(status_code=404, detail="Unknown certificate type")
    try:
        pdf = kyb_service.get_certificate_pdf(session_id, kind)
        if not pdf:
            raise HTTPException(status_code=404, detail="Certificate not issued yet")
        return _pdf_response(pdf, f"{_CERT_FILENAMES[kind]}-{session_id[:8]}.pdf")
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/kyb/{session_id}/credential.pdf")
def kyb_credential_pdf(session_id: str):
    try:
        pdf = kyb_service.get_certificate_pdf(session_id, "compliance")
        if not pdf:
            raise HTTPException(status_code=404, detail="Certificate not issued yet")
        return _pdf_response(pdf, f"tbmc-compliance-certificate-{session_id[:8]}.pdf")
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
