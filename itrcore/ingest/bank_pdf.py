"""PDF bank statements: pypdf decrypt (PAN+DOB-style passwords are common)
then pdfplumber table extraction into the same ColumnMap pipeline."""

import io

import pandas as pd
import pdfplumber
from pypdf import PdfReader, PdfWriter

from itrcore.ingest.base import ColumnMap, FileProbe, ParseResult
from itrcore.ingest.banks import SEEDED_MAPS
from itrcore.ingest.tabular import parse_with_map


def decrypt_if_needed(data: bytes, password: str | None) -> bytes:
    reader = PdfReader(io.BytesIO(data))
    if not reader.is_encrypted:
        return data
    if not password:
        raise ValueError("this PDF is password-protected — enter the "
                         "statement password (often PAN or PAN+DOB)")
    if reader.decrypt(password) == 0:
        raise ValueError("wrong PDF password")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def frame_from_pdf(data: bytes) -> pd.DataFrame:
    """All table rows from all pages, as one raw grid."""
    rows: list[list] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    if raw and any(c not in (None, "") for c in raw):
                        rows.append([(c or "").replace("\n", " ").strip()
                                     for c in raw])
    if not rows:
        raise ValueError("no tables found in the PDF — if this is a "
                         "scanned image, request a CSV/Excel statement")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows)


def parse_pdf(probe: FileProbe, password: str | None = None,
              extra_maps: tuple[ColumnMap, ...] = ()) -> ParseResult:
    data = decrypt_if_needed(probe.data, password)
    frame = frame_from_pdf(data)
    best: ParseResult | None = None
    for cmap in tuple(extra_maps) + SEEDED_MAPS:
        result = parse_with_map(frame, cmap)
        if result.txns and (best is None
                            or len(result.txns) > len(best.txns)):
            best = result
    if best is None:
        return ParseResult(adapter_id="pdf_none", confidence=0.0,
                           issues=("PDF tables found but no bank layout "
                                   "matched — use the manual mapper on a "
                                   "CSV export if possible",))
    return best
