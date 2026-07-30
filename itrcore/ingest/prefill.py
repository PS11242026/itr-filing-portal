"""e-Filing prefill JSON parser: personal info + TDS + bank accounts."""

import json


def parse_prefill_json(data: bytes) -> dict:
    try:
        tree = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a valid prefill JSON: {exc}") from exc

    out = {"pan": None, "name": None, "dob": None, "address": None,
           "email": None, "mobile": None, "bank_accounts": [],
           "tds": []}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                lk = key.lower()
                if lk == "pan" and isinstance(value, str) and not out["pan"]:
                    out["pan"] = value
                elif lk in ("dob", "dateofbirth") and isinstance(value, str):
                    out["dob"] = out["dob"] or value
                elif lk in ("emailaddress", "email") and \
                        isinstance(value, str):
                    out["email"] = out["email"] or value
                elif lk in ("mobileno", "mobile") and \
                        isinstance(value, (str, int)):
                    out["mobile"] = out["mobile"] or str(value)
                elif lk == "ifsccode" and isinstance(node.get(key), str):
                    acc = {"ifsc": value,
                           "bank_name": node.get("BankName")
                           or node.get("bankName") or "",
                           "account_no": str(node.get("BankAccountNo")
                                             or node.get("bankAccountNo")
                                             or "")}
                    if acc["account_no"]:
                        out["bank_accounts"].append(acc)
                elif lk == "tan" and isinstance(value, str):
                    tds_amt = None
                    for k2 in ("TotalTDSSal", "TotTDSDeposited",
                               "ClaimOutOfTotTDSOnAmtPaid", "TDSDeposited"):
                        if node.get(k2) is not None:
                            try:
                                tds_amt = int(float(str(node[k2])))
                            except (TypeError, ValueError):
                                pass
                            break
                    if tds_amt is not None:
                        name_node = node.get(
                            "EmployerOrDeductorOrCollecterName") or ""
                        out["tds"].append({"tan": value,
                                           "deductor": str(name_node)[:100],
                                           "amount": tds_amt})
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tree)
    if not out["pan"]:
        raise ValueError("no PAN found — is this really a prefill JSON?")
    return out
