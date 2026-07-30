"""Taxpayers (encrypted PII), documents (encrypted at rest), checklist."""

from pathlib import Path

import pytest

from itrcore.authz import mint_scope


def test_create_get_and_find_by_pan(partner, taxpayer_id):
    from itrcore.db.repos import taxpayers
    scope = mint_scope(partner, taxpayer_id)
    tp = taxpayers.get_taxpayer(scope)
    assert tp.pan == "AAAPL1234C"
    assert tp.pan_masked == "XXXXX1234C"
    assert taxpayers.find_by_pan(partner, "aaapl1234c") == taxpayer_id
    with pytest.raises(ValueError):
        taxpayers.create_taxpayer(partner, full_name="Dup",
                                  pan="AAAPL1234C")


def test_pan_never_plaintext_in_db(partner, taxpayer_id):
    from itrcore.db import connection
    row = connection.query_one("SELECT * FROM taxpayers WHERE id=?",
                               (taxpayer_id,))
    for key in row.keys():
        value = row[key]
        if isinstance(value, str):
            assert "AAAPL1234C" not in value, f"plaintext PAN in {key}"
        elif isinstance(value, bytes):
            assert b"AAAPL1234C" not in value, f"plaintext PAN in {key}"


def test_document_roundtrip_and_encrypted_at_rest(partner, taxpayer_id):
    from itrcore.db.repos import documents
    scope = mint_scope(partner, taxpayer_id)
    payload = b"Salary account statement April 2025" * 50
    doc_id = documents.save_document(scope, ay="2026-27",
                                     doc_type="bank_statement",
                                     filename="hdfc_apr.csv", data=payload)
    meta, data = documents.load_document(scope, doc_id)
    assert data == payload
    assert meta.orig_name == "hdfc_apr.csv"

    from itrcore.db import connection
    row = connection.query_one("SELECT stored_path FROM documents WHERE id=?",
                               (doc_id,))
    on_disk = Path(row["stored_path"]).read_bytes()
    assert payload not in on_disk, "document stored unencrypted!"
    assert on_disk != payload


def test_checklist_reflects_profile_uploads_and_waivers(partner,
                                                        taxpayer_id):
    from itrcore.db.repos import checklist, documents
    scope = mint_scope(partner, taxpayer_id)
    ay = "2026-27"

    states = {s.item.key: s for s in checklist.status(scope, ay)}
    assert "form16" in states                 # has_salary profile flag
    assert "rent_agreement" not in states     # flag not set
    assert states["form16"].status == "pending"

    documents.save_document(scope, ay=ay, doc_type="form16",
                            filename="form16.pdf", data=b"pdfbytes")
    states = {s.item.key: s for s in checklist.status(scope, ay)}
    assert states["form16"].status == "satisfied"

    checklist.waive(scope, ay, "form26as", "client has no TDS")
    states = {s.item.key: s for s in checklist.status(scope, ay)}
    assert states["form26as"].status == "waived"

    blockers = checklist.blockers(scope, ay)
    assert all("Form 16" not in b and "26AS" not in b for b in blockers)
    assert any("Bank statements" in b for b in blockers)


def test_client_cannot_touch_other_taxpayer_documents(partner, taxpayer_id,
                                                      client_principal):
    from itrcore.authz import AccessDenied
    from itrcore.db.repos import taxpayers
    other_id = taxpayers.create_taxpayer(partner, full_name="Other Person",
                                         pan="AAAPX9999Z")
    with pytest.raises(AccessDenied):
        mint_scope(client_principal, other_id)
