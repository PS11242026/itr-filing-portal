"""Readiness gate: one place that says what blocks finalization/filing."""

from dataclasses import dataclass, field

from itrcore.authz import TaxpayerScope
from itrcore.db.repos import checklist as checklist_repo
from itrcore.db.repos import queries as queries_repo
from itrcore.db.repos import statements as statements_repo
from itrcore.db.repos import transactions as transactions_repo


@dataclass(frozen=True)
class ReadinessReport:
    blockers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.blockers


def readiness(scope: TaxpayerScope, ay: str) -> ReadinessReport:
    blockers: list[str] = []

    counts = transactions_repo.open_counts(scope, ay)
    if counts["total"] and counts["non_terminal"]:
        blockers.append(f"{counts['non_terminal']} of {counts['total']} "
                        "bank transactions are not yet classified to a "
                        "final category")

    open_q = queries_repo.open_count(scope, ay)
    if open_q:
        blockers.append(f"{open_q} client query/queries still open or "
                        "awaiting staff resolution")

    for label in checklist_repo.blockers(scope, ay):
        blockers.append(f"Checklist pending: {label}")

    for gap in statements_repo.coverage_blockers(scope, ay):
        blockers.append(f"Statement coverage: {gap}")

    from itrcore.recon import service as recon_service
    for item in recon_service.blockers(scope, ay):
        blockers.append(f"Reconciliation: {item}")

    return ReadinessReport(blockers=tuple(blockers))
