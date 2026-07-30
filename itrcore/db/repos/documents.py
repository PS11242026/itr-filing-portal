"""Encrypted document store: Fernet blobs on disk + metadata rows.

Files land at data/docstore/<taxpayer_id>/doc<id>.bin, always encrypted;
plaintext exists only in memory (BytesIO for preview/download).
"""

import hashlib
from pathlib import Path

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection
from itrcore.db.crypto import get_cipher
from itrcore.models import DocumentMeta

MAX_DOC_BYTES = 50 * 1024 * 1024


def _row_to_meta(row) -> DocumentMeta:
    return DocumentMeta(
        id=row["id"], taxpayer_id=row["taxpayer_id"], ay=row["ay"],
        doc_type=row["doc_type"], orig_name=row["orig_name"],
        sha256=row["sha256"], size_bytes=row["size_bytes"], note=row["note"],
        uploaded_by=row["uploaded_by"], uploaded_at=row["uploaded_at"],
        status=row["status"])


def save_document(scope: TaxpayerScope, *, ay: str, doc_type: str,
                  filename: str, data: bytes,
                  note: str | None = None) -> int:
    if not data:
        raise ValueError("the uploaded file is empty")
    if len(data) > MAX_DOC_BYTES:
        raise ValueError("file exceeds the 50 MB limit")
    sha = hashlib.sha256(data).hexdigest()
    enc = get_cipher().enc_bytes(data)
    folder = connection.data_dir() / "docstore" / str(scope.taxpayer_id)
    folder.mkdir(parents=True, exist_ok=True)
    with connection.tx() as conn:
        cur = conn.execute(
            "INSERT INTO documents (taxpayer_id, ay, doc_type, orig_name,"
            " stored_path, sha256, size_bytes, note, uploaded_by,"
            " uploaded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (scope.taxpayer_id, ay, doc_type, filename, "", sha, len(data),
             note, scope.principal.username, connection.now()))
        doc_id = cur.lastrowid
        path = folder / f"doc{doc_id}.bin"
        path.write_bytes(enc)
        conn.execute("UPDATE documents SET stored_path=? WHERE id=?",
                     (str(path), doc_id))
    auditlog.log(scope.principal.username, "DOC_UPLOAD", entity="document",
                 entity_id=doc_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "doc_type": doc_type, "name": filename,
                          "sha256": sha})
    return doc_id


def load_document(scope: TaxpayerScope,
                  doc_id: int) -> tuple[DocumentMeta, bytes]:
    row = connection.query_one(
        "SELECT * FROM documents WHERE id=? AND taxpayer_id=?",
        (doc_id, scope.taxpayer_id))
    if row is None:
        raise ValueError("document not found")
    data = get_cipher().dec_bytes(Path(row["stored_path"]).read_bytes())
    auditlog.log(scope.principal.username, "DOC_DOWNLOAD", entity="document",
                 entity_id=doc_id, taxpayer_id=scope.taxpayer_id,
                 details={"name": row["orig_name"]})
    return _row_to_meta(row), data


def list_documents(scope: TaxpayerScope, ay: str | None = None,
                   doc_type: str | None = None,
                   include_void: bool = False) -> list[DocumentMeta]:
    sql = "SELECT * FROM documents WHERE taxpayer_id=?"
    params: list = [scope.taxpayer_id]
    if ay:
        sql += " AND ay=?"
        params.append(ay)
    if doc_type:
        sql += " AND doc_type=?"
        params.append(doc_type)
    if not include_void:
        sql += " AND status='active'"
    sql += " ORDER BY uploaded_at DESC"
    return [_row_to_meta(r) for r in connection.query(sql, tuple(params))]


def void_document(scope: TaxpayerScope, doc_id: int,
                  reason: str = "") -> None:
    require_staff(scope.principal, "Staff", "Voiding documents")
    row = connection.query_one(
        "SELECT id FROM documents WHERE id=? AND taxpayer_id=?",
        (doc_id, scope.taxpayer_id))
    if row is None:
        raise ValueError("document not found")
    connection.execute("UPDATE documents SET status='void' WHERE id=?",
                       (doc_id,))
    auditlog.log(scope.principal.username, "DOC_VOID", entity="document",
                 entity_id=doc_id, taxpayer_id=scope.taxpayer_id,
                 details={"reason": reason})
