"""Indian-format currency helpers (whole rupees)."""


def inr_group(n: int) -> str:
    """1234567 → '12,34,567' (Indian digit grouping, no symbol)."""
    neg = n < 0
    digits = str(abs(int(n)))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return ("-" if neg else "") + digits


def inr(n: int) -> str:
    return "₹" + inr_group(n) if n >= 0 else "-₹" + inr_group(-n)


def rs(n: int) -> str:
    """PDF-safe (latin-1) rendering."""
    return "Rs. " + inr_group(n)
