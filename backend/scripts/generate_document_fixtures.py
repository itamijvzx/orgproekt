"""Generate owned, public synthetic fixtures for the browser checks and manual upload."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.document_fixtures import docx_bytes, pdf_bytes  # noqa: E402

target = Path(sys.argv[1])
target.mkdir(parents=True, exist_ok=True)
(target / "synthetic-regulation.docx").write_bytes(docx_bytes())
(target / "synthetic-regulation.pdf").write_bytes(pdf_bytes())
(target / "synthetic-regulation-after.docx").write_bytes(docx_bytes(after=True))
(target / "synthetic-scan.pdf").write_bytes(pdf_bytes(blank=True))
print("Generated four synthetic document fixtures")
