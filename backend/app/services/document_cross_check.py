"""Deterministic cross-reference of doc extractions vs user + public claims."""

from app.services import kyb_rules

as_text = kyb_rules.as_text


def cross_check_documents(
    user_claims: dict,
    public_facts: dict | None,
    doc_extractions: list[dict],
) -> list[dict]:
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
