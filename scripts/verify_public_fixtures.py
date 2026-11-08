"""Compare publishable DOCX/PDF fixtures to the project's synthetic generators."""

import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from tests.document_fixtures import docx_bytes, pdf_bytes  # noqa: E402


def verify(root):
    fixtures = root / "frontend/e2e/fixtures"
    for filename, after in [
        ("synthetic-regulation.docx", False),
        ("synthetic-regulation-after.docx", True),
    ]:
        with (
            zipfile.ZipFile(fixtures / filename) as actual,
            zipfile.ZipFile(io.BytesIO(docx_bytes(after=after))) as expected,
        ):
            if set(actual.namelist()) != set(expected.namelist()) or any(
                actual.read(name) != expected.read(name) for name in expected.namelist()
            ):
                raise ValueError(filename)
    for filename, blank in [
        ("synthetic-regulation.pdf", False),
        ("synthetic-scan.pdf", True),
    ]:
        if (fixtures / filename).read_bytes() != pdf_bytes(blank=blank):
            raise ValueError(filename)
    print("Verified four public fixtures against synthetic generators")


if __name__ == "__main__":
    try:
        verify(Path(__file__).resolve().parents[1])
    except Exception as error:
        print("Public fixture verification failed: " + type(error).__name__)
        raise SystemExit(1) from None
