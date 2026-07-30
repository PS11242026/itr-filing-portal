"""Mock ERIClient — for tests and demos only. Every acknowledgement number
it fabricates is prefixed MOCK so it can never be mistaken for a real one.
"""

import json
from datetime import datetime

from itrcore.db import connection
from itrcore.efile.protocol import SubmissionResult


class MockERIClient:
    def login(self) -> bool:
        return True

    def add_client(self, pan: str) -> bool:
        return True

    def get_prefill(self, pan: str, ay: str) -> dict:
        return {"pan": pan, "ay": ay, "note": "mock prefill — no data"}

    def submit_return(self, itr_json: dict, form: str,
                      ay: str) -> SubmissionResult:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        outbox = connection.data_dir() / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"mock_submission_{form}_{stamp}.json").write_text(
            json.dumps(itr_json, indent=1), encoding="utf-8")
        return SubmissionResult(success=True, ack_no=f"MOCK{stamp}",
                                messages=("mock submission — not filed "
                                          "with ITD",))

    def everify(self, ack_no: str, method: str) -> bool:
        return ack_no.startswith("MOCK")

    def get_ack(self, ack_no: str) -> dict:
        return {"ack_no": ack_no, "status": "mock"}
