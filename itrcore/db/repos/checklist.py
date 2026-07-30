"""Per-profile required-documents checklist: template + uploads + waivers."""

import json
from functools import lru_cache
from pathlib import Path

import yaml

from itrcore.authz import TaxpayerScope, require_staff
from itrcore.db import auditlog, connection
from itrcore.models import ChecklistItem, ChecklistState

_TEMPLATE_PATH = (Path(__file__).resolve().parents[2] / "config"
                  / "checklist_templates.yaml")


@lru_cache(maxsize=1)
def load_template() -> tuple[ChecklistItem, ...]:
    raw = yaml.safe_load(_TEMPLATE_PATH.read_text(encoding="utf-8"))
    items = []
    for it in raw["items"]:
        items.append(ChecklistItem(
            key=it["key"], label=it["label"], applies=it["applies"],
            doc_types=tuple(it.get("doc_types", ())),
            required=bool(it.get("required", True)),
            help=it.get("help", "")))
    return tuple(items)


def applicable_items(profile: dict) -> list[ChecklistItem]:
    out = []
    for item in load_template():
        if item.applies == "always" or profile.get(item.applies):
            out.append(item)
    return out


def status(scope: TaxpayerScope, ay: str) -> list[ChecklistState]:
    row = connection.query_one(
        "SELECT profile_json FROM taxpayers WHERE id=?",
        (scope.taxpayer_id,))
    profile = json.loads(row["profile_json"] or "{}") if row else {}
    doc_counts: dict[str, int] = {}
    for r in connection.query(
            "SELECT doc_type, COUNT(*) AS c FROM documents"
            " WHERE taxpayer_id=? AND ay=? AND status='active'"
            " GROUP BY doc_type", (scope.taxpayer_id, ay)):
        doc_counts[r["doc_type"]] = r["c"]
    overrides = {r["item_key"]: r for r in connection.query(
        "SELECT * FROM checklist_overrides WHERE taxpayer_id=? AND ay=?",
        (scope.taxpayer_id, ay))}

    out = []
    for item in applicable_items(profile):
        count = sum(doc_counts.get(dt, 0) for dt in item.doc_types)
        ov = overrides.get(item.key)
        if ov is not None:
            state = ("waived" if ov["status"] == "waived" else "satisfied")
            note = ov["note"] or ""
        elif count > 0:
            state, note = "satisfied", ""
        else:
            state, note = "pending", ""
        out.append(ChecklistState(item=item, docs_count=count, status=state,
                                  note=note))
    return out


def blockers(scope: TaxpayerScope, ay: str) -> list[str]:
    """Required items still pending — used by the readiness gate."""
    return [s.item.label for s in status(scope, ay)
            if s.item.required and s.status == "pending"]


def waive(scope: TaxpayerScope, ay: str, item_key: str, note: str) -> None:
    require_staff(scope.principal, "Staff", "Waiving checklist items")
    connection.execute(
        "INSERT INTO checklist_overrides (taxpayer_id, ay, item_key, status,"
        " note, updated_by, updated_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT (taxpayer_id, ay, item_key) DO UPDATE SET"
        " status=excluded.status, note=excluded.note,"
        " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
        (scope.taxpayer_id, ay, item_key, "waived", note,
         scope.principal.username, connection.now()))
    auditlog.log(scope.principal.username, "CHECKLIST_WAIVE",
                 entity="checklist", entity_id=item_key,
                 taxpayer_id=scope.taxpayer_id,
                 details={"ay": ay, "note": note})


def clear_override(scope: TaxpayerScope, ay: str, item_key: str) -> None:
    require_staff(scope.principal, "Staff", "Editing checklist overrides")
    connection.execute(
        "DELETE FROM checklist_overrides WHERE taxpayer_id=? AND ay=?"
        " AND item_key=?", (scope.taxpayer_id, ay, item_key))
    auditlog.log(scope.principal.username, "CHECKLIST_UNWAIVE",
                 entity="checklist", entity_id=item_key,
                 taxpayer_id=scope.taxpayer_id, details={"ay": ay})
