"""VERIFY stage: AI (public presence + doc parse) then deterministic cross-reference."""

from app.services import doc_parser, kyb_rules, llm_search

as_text = kyb_rules.as_text


def _cross_check_documents(
    user_claims: dict,
    public_facts: dict | None,
    doc_extractions: list[dict],
) -> list[dict]:
    """Deterministic cross-reference of AI-extracted doc claims vs user + public inputs."""
    public = public_facts or {}
    legal_name = user_claims.get("legal_name", "")
    ein = user_claims.get("ein", "")
    address = user_claims.get("operating_address", "")
    checks: list[dict] = []

    for doc in doc_extractions:
        extracted = doc.get("extracted") or {}
        label = doc.get("label", doc.get("filename", "document"))
        item_checks: list[dict] = []

        doc_entity = as_text(extracted.get("entity_name") or extracted.get("legal_name"))
        if doc_entity and legal_name:
            item_checks.append(
                {"field": "entity_name", **kyb_rules.check_legal_name(legal_name, doc_entity)}
            )

        doc_ein = as_text(extracted.get("ein"))
        if doc_ein and ein:
            norm_doc = doc_ein.replace(" ", "")
            norm_user = ein.replace(" ", "")
            if norm_doc == norm_user:
                item_checks.append({"field": "ein", "result": "PASS", "detail": "EIN matches uploaded document"})
            else:
                item_checks.append({"field": "ein", "result": "FLAG", "detail": "EIN differs from document extraction"})

        doc_addr = as_text(extracted.get("address"))
        if doc_addr and address:
            item_checks.append({"field": "address", **kyb_rules.check_address(address, doc_addr)})

        pub_name = public.get("legal_name")
        if doc_entity and pub_name:
            item_checks.append(
                {"field": "public_name", **kyb_rules.check_legal_name(doc_entity, pub_name)}
            )

        checks.append(
            {
                "label": label,
                "filename": doc.get("filename"),
                "extracted": extracted,
                "checks": item_checks,
                "note": doc.get("note"),
            }
        )

    return checks


def _should_refresh_public(session: dict, legal_name: str, state: str) -> bool:
    public = session.get("public_facts")
    if not public or not legal_name:
        return bool(legal_name)
    if public.get("confidence", 0) < 0.3:
        return True
    cached_name = (public.get("legal_name") or "").lower()
    if cached_name and legal_name.lower() not in cached_name and cached_name not in legal_name.lower():
        return True
    if state and public.get("incorporation_state") and state.upper() != public.get("incorporation_state"):
        return True
    return False


def _enrich_claims_from_documents(user: dict, extractions: list[dict]) -> None:
    """Fill empty form fields from AI doc extractions when available."""
    for ext in extractions:
        extracted = ext.get("extracted") or {}
        if not user.get("legal_name"):
            name = as_text(extracted.get("entity_name") or extracted.get("legal_name"))
            if name:
                user["legal_name"] = name
        if not user.get("ein"):
            ein = as_text(extracted.get("ein"))
            if ein:
                user["ein"] = ein
        if not user.get("operating_address"):
            addr = as_text(extracted.get("address"))
            if addr:
                user["operating_address"] = addr
        if not user.get("state"):
            addr = as_text(extracted.get("address"))
            state = kyb_rules.extract_state_from_address(addr) if addr else None
            if state:
                user["state"] = state


async def run_verify(
    session: dict,
    uploads: list[tuple[str, str, bytes]],
    *,
    refresh_public: bool | None = None,
) -> dict:
    """
    VERIFY pipeline (matches diagram stage 2):
      A) AI — public internet presence + per-document structured extraction
      B) Deterministic — 10-point scorecard + doc claim cross-reference
    Raw document bytes are processed in memory and discarded after this call.
    """
    user = session["user_claims"]
    legal_name = user.get("legal_name", "")
    state = user.get("state", "")

    # --- AI: parse documents first (may supply name, EIN, address) ---
    doc_extractions = await doc_parser.parse_uploads(uploads)
    _enrich_claims_from_documents(user, doc_extractions)
    legal_name = user.get("legal_name", "")
    state = user.get("state", "")

    if refresh_public is None:
        refresh_public = _should_refresh_public(session, legal_name, state)
    elif not refresh_public and legal_name and not session.get("public_facts"):
        refresh_public = True

    # --- AI: public presence ---
    public_facts = session.get("public_facts")
    if refresh_public and legal_name:
        public_facts = await llm_search.search_company_public_info(legal_name, state)
        session["public_facts"] = public_facts

    ai_public = {
        "legal_name": legal_name or None,
        "state": state or None,
        "public_facts": public_facts,
        "search_method": (public_facts or {}).get("search_method"),
        "confidence": (public_facts or {}).get("confidence"),
    }

    ai_documents = {
        "count": len(doc_extractions),
        "extractions": [
            {
                "label": d.get("label"),
                "filename": d.get("filename"),
                "extracted": d.get("extracted", {}),
                "text_length": d.get("text_length", 0),
                "note": d.get("note"),
            }
            for d in doc_extractions
        ],
    }

    # --- Deterministic: scorecard + doc cross-reference ---
    session["documents"] = [
        {"label": d.get("label"), "filename": d.get("filename")} for d in doc_extractions
    ]
    session["doc_extractions"] = ai_documents["extractions"]

    scorecard = kyb_rules.build_scorecard(session)
    doc_cross_checks = _cross_check_documents(user, public_facts, doc_extractions)

    return {
        "stage": "verify",
        "ai": {
            "public_presence": ai_public,
            "documents": ai_documents,
        },
        "deterministic": {
            "scorecard": scorecard,
            "document_cross_checks": doc_cross_checks,
        },
    }
