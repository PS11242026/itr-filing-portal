# Vendored ITR JSON schemas — AY 2026-27

| File | Form | Schema version | Source | Downloaded | SHA-256 |
|---|---|---|---|---|---|
| ITR1_Ver1.0.json | ITR-1 | Ver1.0 (PORTAL-DERIVED PLACEHOLDER) | built from the documented ITR-1 JSON structure | 2026-07-19 | — |

## ⚠️ Replace the placeholder with the official schema

The file currently here is a **portal-derived placeholder**: it describes
exactly the JSON this portal's builder produces, so structural validation
works end-to-end today. Before relying on portal-side validation for real
filings, drop in the official ITD schema:

1. Get the official schema, either from
   **incometax.gov.in → Downloads → Income Tax Returns → AY 2026-27 →
   ITR-1 → "Schema"**, or from the offline utility's install folder
   (`…\ITD e-Filing Utility\assets\schema\` inside the extracted utility).
2. Copy it here as `ITR1_<version>.json` (keep the official version string
   in the filename).
3. Record the source URL, date and `Get-FileHash -Algorithm SHA256` value
   in the table above.
4. Add the version to `SUPPORTED_VERSIONS` in
   `itrcore/itr/schema_registry.py`.
5. Run `pytest tests/unit/test_itr1.py` — any key-name differences between
   the official schema and the builder output belong in
   `itrcore/itr/quirks.py` (version-keyed transforms), not in the builder.

**Regardless of schema status, the SOP is: import every generated JSON into
the official offline utility and let it validate before uploading.**

## Adding ITR-3 / ITR-4 (business returns)

Business returns route to **ITR-4** (presumptive 44AD/44ADA/44AE) or **ITR-3**
(regular books). The official schema ships inside the ITD offline utility
(`…\ITD e-Filing Utility\assets\schema\`) — there is no clean public JSON URL,
so extract it and vendor it with the helper:

```
python scripts/vendor_schema.py --form ITR-4 --ay 2026-27 \
    --version <ver> --source <path-or-https-url>
```

Then register the version in `schema_registry.SUPPORTED_VERSIONS`, add the
builder (`itrcore/itr/itr4.py` / `itr3.py`), put any key-name deltas in
`quirks.py`, and run `python -m pytest`. Until a builder + schema are in,
business income computes and exports to Excel, and the filing page reports
that ITR-3/4 file generation is pending.
