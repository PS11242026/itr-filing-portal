"""Persistence for AIS / Form 26AS imports and saved column maps."""

import json

from itrcore.authz import Principal, TaxpayerScope, require_staff
from itrcore.db import auditlog, connection


def import_ais(scope: TaxpayerScope, ay: str, entries: list[dict],
               source_file: str) -> int:
    require_staff(scope.principal, "Staff", "Importing AIS")
    with connection.tx() as conn:
        conn.execute("DELETE FROM ais_entries WHERE taxpayer_id=? AND ay=?",
                     (scope.taxpayer_id, ay))
        conn.executemany(
            "INSERT INTO ais_entries (taxpayer_id, ay, category,"
            " description, amount_paise, source_file, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            [(scope.taxpayer_id, ay, e["category"], e["description"],
              e["amount_paise"], source_file, connection.now())
             for e in entries])
    auditlog.log(scope.principal.username, "AIS_IMPORT", entity="ais",
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "rows": len(entries),
                          "file": source_file})
    return len(entries)


def list_ais(scope: TaxpayerScope, ay: str):
    return connection.query(
        "SELECT * FROM ais_entries WHERE taxpayer_id=? AND ay=?"
        " ORDER BY category, id", (scope.taxpayer_id, ay))


def import_tis(scope: TaxpayerScope, ay: str, entries: list[dict],
               source_file: str) -> int:
    require_staff(scope.principal, "Staff", "Importing TIS")
    with connection.tx() as conn:
        conn.execute("DELETE FROM tis_entries WHERE taxpayer_id=? AND ay=?",
                     (scope.taxpayer_id, ay))
        conn.executemany(
            "INSERT INTO tis_entries (taxpayer_id, ay, category, description,"
            " processed_value_paise, derived_value_paise, source_file,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            [(scope.taxpayer_id, ay, e["category"], e["description"],
              e["processed_value_paise"], e["derived_value_paise"],
              source_file, connection.now()) for e in entries])
    auditlog.log(scope.principal.username, "TIS_IMPORT", entity="tis",
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "rows": len(entries),
                          "file": source_file})
    return len(entries)


def list_tis(scope: TaxpayerScope, ay: str):
    return connection.query(
        "SELECT * FROM tis_entries WHERE taxpayer_id=? AND ay=?"
        " ORDER BY category, id", (scope.taxpayer_id, ay))


def import_26as(scope: TaxpayerScope, ay: str, entries: list[dict]) -> int:
    require_staff(scope.principal, "Staff", "Importing 26AS")
    with connection.tx() as conn:
        conn.execute(
            "DELETE FROM tds_entries WHERE taxpayer_id=? AND ay=?"
            " AND source='26as'", (scope.taxpayer_id, ay))
        conn.executemany(
            "INSERT INTO tds_entries (taxpayer_id, ay, source, tan,"
            " deductor_name, section, amount_paid_paise, tds_paise,"
            " created_at) VALUES (?,?,'26as',?,?,?,?,?,?)",
            [(scope.taxpayer_id, ay, e.get("tan"), e.get("deductor_name"),
              e.get("section"), e.get("amount_paid_paise"),
              e["tds_paise"], connection.now()) for e in entries])
    auditlog.log(scope.principal.username, "26AS_IMPORT", entity="26as",
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "rows": len(entries)})
    return len(entries)


def import_challans(scope: TaxpayerScope, ay: str,
                    challans: list[dict]) -> int:
    """Store advance / self-assessment challans read from a statement.

    Used by the sources that do NOT persist a parsed document of their own —
    the TRACES text/CSV 26AS export. The AIS and the 26AS PDF keep their
    documents, so auto-fill re-derives their challans on the fly and they
    never go stale here."""
    require_staff(scope.principal, "Staff", "Importing tax challans")
    rows = [c for c in challans
            if c.get("paid_on") and int(c.get("amount", 0) or 0) > 0
            and c.get("kind") in ("advance", "self_assessment")]
    with connection.tx() as conn:
        conn.execute("DELETE FROM challans WHERE taxpayer_id=? AND ay=?",
                     (scope.taxpayer_id, ay))
        conn.executemany(
            "INSERT INTO challans (taxpayer_id, ay, kind, bsr_code,"
            " challan_no, paid_on, amount_paise, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [(scope.taxpayer_id, ay, c["kind"],
              str(c.get("reference", "")).split(" - ")[0] or None,
              (str(c.get("reference", "")).split(" - ") + [""])[1] or None,
              c["paid_on"], int(c["amount"]) * 100, connection.now())
             for c in rows])
    auditlog.log(scope.principal.username, "CHALLAN_IMPORT", entity="challans",
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "rows": len(rows)})
    return len(rows)


def list_challans(scope: TaxpayerScope, ay: str) -> list[dict]:
    """Stored challans, shaped for the computation's `challans` input."""
    rows = connection.query(
        "SELECT kind, bsr_code, challan_no, paid_on, amount_paise FROM"
        " challans WHERE taxpayer_id=? AND ay=? ORDER BY paid_on",
        (scope.taxpayer_id, ay))
    return [{"kind": r["kind"], "paid_on": r["paid_on"],
             "amount": round(r["amount_paise"] / 100),
             "reference": " - ".join(x for x in (r["bsr_code"],
                                                 r["challan_no"]) if x)}
            for r in rows]


def list_tds(scope: TaxpayerScope, ay: str, source: str | None = None):
    sql = "SELECT * FROM tds_entries WHERE taxpayer_id=? AND ay=?"
    params: list = [scope.taxpayer_id, ay]
    if source:
        sql += " AND source=?"
        params.append(source)
    return connection.query(sql + " ORDER BY id", tuple(params))


def save_form16(scope: TaxpayerScope, ay: str, *, employer_name: str,
                employer_tan: str, gross_salary_paise: int,
                exempt_allowances_paise: int, prof_tax_paise: int,
                tds_paise: int, source_doc_id: int | None = None) -> int:
    require_staff(scope.principal, "Staff", "Entering Form 16")
    fid = connection.execute(
        "INSERT INTO form16_data (taxpayer_id, ay, employer_name,"
        " employer_tan, gross_salary_paise, exempt_allowances_paise,"
        " prof_tax_paise, tds_paise, source_doc_id, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (scope.taxpayer_id, ay, employer_name, employer_tan,
         gross_salary_paise, exempt_allowances_paise, prof_tax_paise,
         tds_paise, source_doc_id, connection.now()))
    auditlog.log(scope.principal.username, "FORM16_ENTRY",
                 entity="form16", entity_id=fid,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "employer": employer_name})
    return fid


def list_form16(scope: TaxpayerScope, ay: str):
    return connection.query(
        "SELECT * FROM form16_data WHERE taxpayer_id=? AND ay=?"
        " ORDER BY id", (scope.taxpayer_id, ay))


# ---- full parsed source documents (AIS with L1+L2 detail, 26AS parts) ----

def _save_document(scope: TaxpayerScope, ay: str, table: str, action: str,
                   entity: str, doc: dict, source_file: str) -> None:
    with connection.tx() as conn:
        conn.execute(
            f"INSERT INTO {table} (taxpayer_id, ay, source_file, doc_json,"
            " created_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT (taxpayer_id, ay) DO UPDATE SET"
            " doc_json=excluded.doc_json, source_file=excluded.source_file,"
            " created_at=excluded.created_at",
            (scope.taxpayer_id, ay, source_file, json.dumps(doc),
             connection.now()))
    auditlog.log(scope.principal.username, action, entity=entity,
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "file": source_file})


def _get_document(scope: TaxpayerScope, ay: str, table: str) -> dict | None:
    rows = connection.query(
        f"SELECT doc_json, source_file, created_at FROM {table}"
        " WHERE taxpayer_id=? AND ay=?", (scope.taxpayer_id, ay))
    if not rows:
        return None
    return {"doc": json.loads(rows[0]["doc_json"]),
            "source_file": rows[0]["source_file"],
            "created_at": rows[0]["created_at"]}


def save_ais_document(scope: TaxpayerScope, ay: str, doc: dict,
                      source_file: str) -> None:
    require_staff(scope.principal, "Staff", "Saving AIS document")
    _save_document(scope, ay, "ais_documents", "AIS_DOC_SAVE", "ais", doc,
                   source_file)


def get_ais_document(scope: TaxpayerScope, ay: str) -> dict | None:
    return _get_document(scope, ay, "ais_documents")


def list_ais_documents(scope: TaxpayerScope) -> list[dict]:
    """Every stored AIS document for this client, newest AY first.

    Needed because a challan is reported in the AIS of the year it was PAID
    while it discharges the year it RELATES to: self-assessment tax for
    AY 2026-27 paid in July 2026 falls in FY 2026-27, so it only ever shows
    up in the FOLLOWING year's AIS. Reading one AY's document alone can
    never find it."""
    rows = connection.query(
        "SELECT ay, doc_json, source_file, created_at FROM ais_documents"
        " WHERE taxpayer_id=? ORDER BY ay DESC", (scope.taxpayer_id,))
    return [{"ay": r["ay"], "doc": json.loads(r["doc_json"]),
             "source_file": r["source_file"], "created_at": r["created_at"]}
            for r in rows]


def refresh_ais_derived(scope: TaxpayerScope, ay: str) -> bool:
    """Self-healing: re-derive the flat AIS entries and the TIS rows from
    the saved AIS document with the CURRENT parser (classification,
    de-duplication rules), and replace the stored rows when they differ.

    Called by the Statements/Computation pages on render, so parser fixes
    (e.g. the SFT-only dividend rule) reach every already-imported client
    automatically — no manual re-upload. Returns True when rows changed.
    """
    require_staff(scope.principal, "Staff", "Refreshing AIS-derived rows")
    saved = get_ais_document(scope, ay)
    if not saved:
        return False
    from itrcore.ingest import ais as ais_mod   # parsing stays in ingest
    doc = ais_mod.document_from_dict(saved["doc"])   # buckets recomputed
    fresh_ais = ais_mod.flat_entries(doc)
    fresh_tis = ais_mod.tis_entries(doc)

    current_ais_rows = list_ais(scope, ay)
    current_tis_rows = list_tis(scope, ay)
    ais_changed = {(r["category"], r["amount_paise"])
                   for r in current_ais_rows} \
        != {(e["category"], e["amount_paise"]) for e in fresh_ais}
    # The ITD's own downloaded TIS is authoritative — refresh must never
    # overwrite it with values derived from the AIS document.
    tis_is_real = any("(TIS download)" in str(r["source_file"] or "")
                      for r in current_tis_rows)
    tis_changed = not tis_is_real and (
        {(r["category"], r["processed_value_paise"],
          r["derived_value_paise"]) for r in current_tis_rows}
        != {(e["category"], e["processed_value_paise"],
             e["derived_value_paise"]) for e in fresh_tis})
    if not ais_changed and not tis_changed:
        return False

    source = saved["source_file"] or "saved AIS document"
    if ais_changed:
        import_ais(scope, ay, fresh_ais, source)
    if tis_changed:
        import_tis(scope, ay, fresh_tis, source)
    # Persist the recomputed buckets so the stored doc matches what the
    # viewer and future refreshes see.
    save_ais_document(scope, ay, ais_mod.document_to_dict(doc), source)
    auditlog.log(scope.principal.username, "AIS_REFRESH", entity="ais",
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "ais_rows": len(fresh_ais),
                          "tis_rows": len(fresh_tis) if tis_changed else 0,
                          "tis_preserved": tis_is_real})
    return True


def save_26as_document(scope: TaxpayerScope, ay: str, doc: dict,
                       source_file: str) -> None:
    require_staff(scope.principal, "Staff", "Saving 26AS document")
    _save_document(scope, ay, "form26as_documents", "26AS_DOC_SAVE", "26as",
                   doc, source_file)


def get_26as_document(scope: TaxpayerScope, ay: str) -> dict | None:
    return _get_document(scope, ay, "form26as_documents")


# ---- parked encrypted AIS payloads (await the client's DOB) ----

def save_pending_ais(scope: TaxpayerScope, ay: str, payload: bytes,
                     source_file: str) -> None:
    """Park an AIS payload that could not be decrypted yet (no DOB on the
    client record). It is imported automatically by the next page render
    once the DOB exists — no re-pull, no manual re-upload."""
    require_staff(scope.principal, "Staff", "Parking AIS payload")
    with connection.tx() as conn:
        conn.execute(
            "INSERT INTO pending_ais_payloads (taxpayer_id, ay, payload,"
            " source_file, created_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT (taxpayer_id, ay) DO UPDATE SET"
            " payload=excluded.payload, source_file=excluded.source_file,"
            " created_at=excluded.created_at",
            (scope.taxpayer_id, ay, payload, source_file, connection.now()))
    auditlog.log(scope.principal.username, "AIS_PARKED", entity="ais",
                 entity_id=scope.taxpayer_id, taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "file": source_file,
                          "bytes": len(payload)})


def get_pending_ais(scope: TaxpayerScope, ay: str) -> dict | None:
    rows = connection.query(
        "SELECT payload, source_file, created_at FROM pending_ais_payloads"
        " WHERE taxpayer_id=? AND ay=?", (scope.taxpayer_id, ay))
    if not rows:
        return None
    return {"payload": rows[0]["payload"],
            "source_file": rows[0]["source_file"],
            "created_at": rows[0]["created_at"]}


def clear_pending_ais(scope: TaxpayerScope, ay: str) -> None:
    require_staff(scope.principal, "Staff", "Clearing parked AIS payload")
    connection.execute(
        "DELETE FROM pending_ais_payloads WHERE taxpayer_id=? AND ay=?",
        (scope.taxpayer_id, ay))
    auditlog.log(scope.principal.username, "AIS_PARKED_CLEARED",
                 entity="ais", entity_id=scope.taxpayer_id,
                 taxpayer_id=scope.taxpayer_id, details={"ay": ay})


# ---- saved column maps (global config, staff-managed) ----

def saved_maps(principal: Principal) -> list[dict]:
    require_staff(principal, "Staff", "Reading saved column maps")
    return [dict(r) for r in connection.query(
        "SELECT * FROM column_maps ORDER BY id DESC")]


def save_map(principal: Principal, *, header_signature: str,
             bank_hint: str, map_json: str) -> None:
    require_staff(principal, "Staff", "Saving column maps")
    connection.execute(
        "INSERT INTO column_maps (header_signature, bank_hint, map_json,"
        " created_by, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT (header_signature) DO UPDATE SET"
        " map_json=excluded.map_json, bank_hint=excluded.bank_hint,"
        " last_used_at=excluded.created_at",
        (header_signature, bank_hint, map_json, principal.username,
         connection.now()))
    auditlog.log(principal.username, "COLUMN_MAP_SAVE", entity="column_map",
                 entity_id=header_signature, details={"bank": bank_hint})
