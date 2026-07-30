"""Authorization primitives: Principal, TaxpayerScope and mint_scope().

TaxpayerScope is the only credential the repository layer accepts for
taxpayer-scoped data. It cannot be constructed directly — only
mint_scope() can create one, and it refuses cross-taxpayer access for
client principals. This module is the security boundary; the Streamlit
navigation and page guards above it are UX, not enforcement.
"""

from dataclasses import dataclass, field

STAFF_ROLES = ("Staff", "Manager", "Partner")
ALL_ROLES = STAFF_ROLES + ("Client",)
_RANK = {"Staff": 1, "Manager": 2, "Partner": 3}


class AccessDenied(PermissionError):
    """Raised when a principal attempts an action outside its authority."""


@dataclass(frozen=True)
class Principal:
    """An authenticated user. For Client role, taxpayer_id is their own
    taxpayer record; for staff roles it is None."""

    user_id: int
    username: str
    role: str
    taxpayer_id: int | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES


def has_role(principal: Principal | None, min_role: str) -> bool:
    """Staff hierarchy check (Staff < Manager < Partner). Clients rank 0."""
    if principal is None:
        return False
    return _RANK.get(principal.role, 0) >= _RANK[min_role]


def require_staff(principal: Principal, min_role: str = "Staff",
                  action: str = "") -> None:
    if not has_role(principal, min_role):
        raise AccessDenied(
            f"{action or 'This action'} requires role {min_role} or above")


# Module-private proof token: repos can trust any TaxpayerScope instance
# because constructing one without mint_scope() raises.
_MINT = object()


@dataclass(frozen=True)
class TaxpayerScope:
    taxpayer_id: int
    principal: Principal
    proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.proof is not _MINT:
            raise AccessDenied(
                "TaxpayerScope must be created via mint_scope()")


def mint_scope(principal: Principal, taxpayer_id: int) -> TaxpayerScope:
    """Create a scope for one taxpayer's data.

    Staff may scope to any taxpayer; a client only to their own. Denied
    attempts are audit-logged and raise AccessDenied. Successful mints are
    not logged (one occurs per page rerun); sensitive operations log at the
    repo level instead.
    """
    if taxpayer_id is None:
        raise AccessDenied("taxpayer_id is required")
    taxpayer_id = int(taxpayer_id)
    if principal.is_staff or principal.taxpayer_id == taxpayer_id:
        return TaxpayerScope(taxpayer_id, principal, _MINT)

    from itrcore.db import auditlog  # local import to avoid cycles
    auditlog.log(principal.username, "SCOPE_DENIED", entity="taxpayer",
                 entity_id=taxpayer_id, taxpayer_id=principal.taxpayer_id,
                 details={"attempted": taxpayer_id})
    raise AccessDenied("You do not have access to this taxpayer's data")
