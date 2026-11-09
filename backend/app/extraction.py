"""Bounded, local-only parsing. Never interpret document instructions or fetch relationships."""

import io
import json
import re
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, field

from defusedxml import ElementTree as ET

PARSER_VERSION = "orgproekt-extract-v1"
MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 1_000_000
MAX_BLOCKS = 2000
MAX_FRAGMENT = 2000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ExtractionError(Exception):
    def __init__(self, code):
        self.code = code


@dataclass
class Block:
    kind: str
    locator: str
    text: str
    page: int | None = None
    structure: dict = field(default_factory=dict)


@dataclass
class Extraction:
    status: str
    blocks: list[Block]
    warnings: list[str]
    page_count: int | None = None


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def spans(value: str):
    """Exact, non-overlapping ranges. Paragraph/cell/page boundaries are never crossed."""
    start = 0
    while start < len(value):
        end = min(start + MAX_FRAGMENT, len(value))
        if end < len(value):
            boundary = max(
                value.rfind("\n", start + MAX_FRAGMENT // 2, end),
                value.rfind(" ", start + MAX_FRAGMENT // 2, end),
            )
            if boundary > start:
                end = boundary + 1
        if value[start:end].strip():
            yield start, end
        start = end


def validate_blocks(blocks):
    if (
        len(blocks) > MAX_BLOCKS
        or sum(len(b.text) for b in blocks) > MAX_TEXT
        or any(len(b.text) > 50_000 for b in blocks)
    ):
        raise ExtractionError("text_limit")
    if any("\x00" in b.text for b in blocks):
        raise ExtractionError("invalid_text")


def docx(data: bytes) -> Extraction:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise ExtractionError("invalid_docx") from error
    with archive:
        entries = archive.infolist()
        if len(entries) > 1000:
            raise ExtractionError("archive_limit")
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ExtractionError("invalid_docx")
        total = 0
        for entry in entries:
            name = entry.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/") or ":" in name:
                raise ExtractionError("unsafe_archive")
            if entry.flag_bits & 1 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ExtractionError("unsafe_archive")
            total += entry.file_size
            if (
                entry.file_size > 16 * 1024 * 1024
                or total > 50 * 1024 * 1024
                or entry.file_size > max(entry.compress_size, 1) * 200
            ):
                raise ExtractionError("archive_limit")
            if any(part in name.lower() for part in ("vbaproject", "embeddings/", "activex/")):
                raise ExtractionError("active_content")
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise ExtractionError("invalid_docx")
        content_types = archive.read("[Content_Types].xml")
        ET.fromstring(content_types, forbid_dtd=True)
        if b"macroEnabled" in content_types or b"vbaProject" in content_types:
            raise ExtractionError("active_content")
        warnings = set()
        # Parse every XML part through a DTD/entity-forbidding parser before processing it.
        trees = {}
        for name in names:
            if name.endswith((".xml", ".rels")):
                trees[name] = ET.fromstring(archive.read(name), forbid_dtd=True)
                if name.endswith(".rels") and any(
                    (item.get("TargetMode") or "").casefold() == "external" for item in trees[name]
                ):
                    raise ExtractionError("active_content")
        root = trees["word/document.xml"]
        body = root.find(W + "body")
        if body is None:
            raise ExtractionError("invalid_docx")
        if any(
            item.tag in {W + "instrText", W + "fldSimple", W + "fldChar"} for item in body.iter()
        ):
            raise ExtractionError("active_content")
        partial = any(
            name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes"))
            for name in names
        )
        if partial:
            warnings.add("ancillary_parts_not_extracted")
        unsupported = {
            W + tag
            for tag in (
                "ins",
                "del",
                "moveFrom",
                "moveTo",
                "altChunk",
                "txbxContent",
                "sdt",
                "drawing",
                "pict",
            )
        }
        if any(item.tag in unsupported for item in body.iter()):
            partial = True
            warnings.add("unsupported_docx_content")
        blocks = []
        heading = ""

        def walk(container, prefix="", depth=0):
            nonlocal heading
            if depth > 8:
                raise ExtractionError("structure_limit")
            paragraph = table = 0
            for element in container:
                if element.tag == W + "p":
                    paragraph += 1
                    # Runs and hyperlinks are textual data; fields, deleted text and drawings aren't executed.
                    pieces = []
                    for node in element.iter():
                        if node.tag == W + "t":
                            pieces.append(node.text or "")
                        elif node.tag == W + "tab":
                            pieces.append("\t")
                        elif node.tag in {W + "br", W + "cr"}:
                            pieces.append("\n")
                    value = "".join(pieces)
                    style = element.find(f"{W}pPr/{W}pStyle")
                    style_name = style.get(W + "val", "") if style is not None else ""
                    numbering = element.find(f"{W}pPr/{W}numPr")
                    kind = "heading" if re.fullmatch(r"Heading[1-9]", style_name) else "paragraph"
                    if kind == "heading":
                        heading = value[:200]
                    structure = {"style": style_name[:100], "heading": heading}
                    if numbering is not None:
                        kind = "list_item"
                        structure["numbering"] = {
                            child.tag.split("}")[-1]: child.get(W + "val") for child in numbering
                        }
                        warnings.add("automatic_numbering_not_rendered")
                    if value.strip():
                        locator = f"{prefix}Абзац {paragraph}"
                        if len(locator) > 120:
                            raise ExtractionError("structure_limit")
                        blocks.append(Block(kind, locator, value, structure=structure))
                        validate_blocks(blocks)
                elif element.tag == W + "tbl":
                    table += 1
                    for ri, row in enumerate(element.findall(W + "tr"), 1):
                        for ci, cell in enumerate(row.findall(W + "tc"), 1):
                            walk(
                                cell,
                                f"{prefix}Таблица {table}, строка {ri}, ячейка {ci}; ",
                                depth + 1,
                            )

        walk(body)
        if not blocks:
            return Extraction("needs_ocr", [], sorted(warnings | {"no_text_layer"}))
        return Extraction("partial" if partial else "ready", blocks, sorted(warnings))


def pdf(data: bytes) -> Extraction:
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    reader = PdfReader(io.BytesIO(data), strict=True)
    if reader.is_encrypted:
        raise ExtractionError("encrypted_pdf")
    if len(reader.pages) > 100:
        raise ExtractionError("page_limit")
    seen = set()
    visited = 0

    def inspect(value, depth=0):
        nonlocal visited
        if depth > 50:
            raise ExtractionError("structure_limit")
        if isinstance(value, IndirectObject):
            key = (value.idnum, value.generation)
            if key in seen:
                return
            seen.add(key)
            value = value.get_object()
        visited += 1
        if visited > 100_000:
            raise ExtractionError("structure_limit")
        if isinstance(value, DictionaryObject):
            if value.get("/Type") == "/EmbeddedFile" or value.get("/Subtype") == "/FileAttachment":
                raise ExtractionError("active_content")
            if any(
                key in value
                for key in (
                    "/JS",
                    "/JavaScript",
                    "/Launch",
                    "/EmbeddedFiles",
                    "/XFA",
                    "/RichMedia",
                    "/URI",
                )
            ):
                raise ExtractionError("active_content")
            if value.get("/S") in (
                "/JavaScript",
                "/Launch",
                "/GoToR",
                "/SubmitForm",
                "/ImportData",
                "/URI",
            ):
                raise ExtractionError("active_content")
            for item in value.values():
                inspect(item, depth + 1)
        elif isinstance(value, ArrayObject):
            for item in value:
                inspect(item, depth + 1)

    inspect(reader.trailer)
    blocks = []
    empty_pages = []
    for number, page in enumerate(reader.pages, 1):
        contents = page.get_contents()
        if contents is not None and len(contents.get_data()) > 8 * 1024 * 1024:
            raise ExtractionError("stream_limit")
        value = page.extract_text(extraction_mode="layout")
        if not value.strip():
            empty_pages.append(number)
        else:
            blocks.append(Block("pdf_page", f"Страница {number}", value, page=number))
            validate_blocks(blocks)
    warnings = ["pdf_layout_requires_review"]
    if empty_pages:
        warnings.append("pages_without_text:" + ",".join(map(str, empty_pages)))
    if "/AcroForm" in reader.trailer["/Root"]:
        warnings.append("form_fields_not_extracted")
    status = (
        "needs_ocr" if not blocks else "partial" if empty_pages or len(warnings) > 1 else "ready"
    )
    return Extraction(status, blocks, warnings, len(reader.pages))


def extract(data: bytes, file_format: str) -> Extraction:
    if not data or len(data) > MAX_BYTES:
        raise ExtractionError("file_limit")
    try:
        if file_format == "docx" and data.startswith(b"PK\x03\x04"):
            return docx(data)
        if file_format == "pdf" and data.startswith(b"%PDF-"):
            return pdf(data)
        raise ExtractionError("invalid_format")
    except ExtractionError:
        raise
    except Exception as error:
        raise ExtractionError("parse_failed") from error


def main():
    """Child process: stdin bytes → bounded JSON. No DB, credentials, file paths or networking."""
    import logging
    import resource
    import sys

    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    logging.disable(logging.CRITICAL)
    try:
        value = extract(sys.stdin.buffer.read(MAX_BYTES + 1), sys.argv[1])
        payload = {"ok": True, **asdict(value)}
    except ExtractionError as error:
        payload = {"ok": False, "error_code": error.code}
    except BaseException:
        payload = {"ok": False, "error_code": "resource_limit"}
    sys.stdout.write(json.dumps(payload, ensure_ascii=True))


if __name__ == "__main__":
    main()
