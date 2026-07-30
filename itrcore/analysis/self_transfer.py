"""Self-transfer pairing across the taxpayer's own accounts + reversals."""


_TRANSFER_MODES = {"NEFT", "IMPS", "RTGS", "UPI", "TPT", "FT", "MMT", None}


def pair_self_transfers(txns: list[dict], window_days: int) -> set[int]:
    """Match debit −X in one account with credit +X in another within the
    window. Greedy: exact amount, nearest date first. Returns txn ids."""
    debits = [t for t in txns if t["amount_paise"] < 0
              and t.get("mode") in _TRANSFER_MODES]
    credits = [t for t in txns if t["amount_paise"] > 0
               and t.get("mode") in _TRANSFER_MODES]
    used: set[int] = set()
    matched: set[int] = set()
    for d in sorted(debits, key=lambda t: t["txn_date"]):
        best = None
        for c in credits:
            if c["id"] in used or c["account_id"] == d["account_id"]:
                continue
            if c["amount_paise"] != -d["amount_paise"]:
                continue
            delta = abs((c["txn_date"] - d["txn_date"]).days)
            if delta > window_days:
                continue
            if best is None or delta < best[0]:
                best = (delta, c)
        if best is not None:
            _, c = best
            used.add(c["id"])
            matched.update((d["id"], c["id"]))
    return matched


def pair_reversals(txns: list[dict], window_days: int) -> set[int]:
    """Same account + same counterparty + equal-opposite amounts within a
    short window → both legs are noise (failed/reversed transactions)."""
    matched: set[int] = set()
    by_acct: dict = {}
    for t in txns:
        by_acct.setdefault(t["account_id"], []).append(t)
    for members in by_acct.values():
        members.sort(key=lambda t: t["txn_date"])
        for i, a in enumerate(members):
            if a["id"] in matched:
                continue
            for b in members[i + 1:]:
                if b["id"] in matched:
                    continue
                if (b["txn_date"] - a["txn_date"]).days > window_days:
                    break
                if (a["counterparty_key"] == b["counterparty_key"]
                        and a["amount_paise"] == -b["amount_paise"]):
                    matched.update((a["id"], b["id"]))
                    break
    return matched
