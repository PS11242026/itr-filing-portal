"""ERIClient protocol.

The firm is not a registered ERI, so v1 ships only MockERIClient and the
manual-upload SOP (export JSON → upload on incometax.gov.in → e-verify).
When real access arrives (own Type-2 ERI registration or a commercial API
provider), implement this protocol and swap it in — the filing page and
tests are already written against it.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SubmissionResult:
    success: bool
    ack_no: str = ""
    messages: tuple[str, ...] = field(default_factory=tuple)


class ERIClient(Protocol):
    def login(self) -> bool: ...

    def add_client(self, pan: str) -> bool: ...

    def get_prefill(self, pan: str, ay: str) -> dict: ...

    def submit_return(self, itr_json: dict, form: str,
                      ay: str) -> SubmissionResult: ...

    def everify(self, ack_no: str, method: str) -> bool: ...

    def get_ack(self, ack_no: str) -> dict: ...
