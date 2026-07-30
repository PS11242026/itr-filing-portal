"""File-kind detection. Many Indian bank '.xls' downloads are actually HTML
tables; true content wins over the file extension."""

from itrcore.ingest.base import FileProbe


def make_probe(filename: str, data: bytes) -> FileProbe:
    head = data[:4096]
    text_head = ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text_head = head.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lower = text_head.lstrip().lower()

    if data[:4] == b"%PDF":
        kind = "pdf"
    elif data[:2] == b"PK":
        kind = "xlsx"
    elif lower.startswith(("<html", "<!doctype", "<table", "<?xml")):
        kind = "xls_html"
    elif data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        kind = "xls_legacy"
    elif filename.lower().endswith((".csv", ".txt")) or "," in text_head:
        kind = "csv"
    else:
        kind = "unknown"
    return FileProbe(filename=filename, data=data, kind=kind,
                     text_head=text_head)
