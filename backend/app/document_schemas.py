from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JobStatus = Literal[
    "queued", "processing", "ready", "partial", "needs_ocr", "failed", "cancelled", "access_revoked"
]


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department_id: str = Field(min_length=36, max_length=36)
    title: str = Field(min_length=3, max_length=240)
    revision: str = Field(min_length=1, max_length=30)
    document_id: str | None = Field(None, min_length=36, max_length=36)
    expected_version: int = Field(0, ge=0)
    document_kind: Literal["regulation", "job_description", "personnel_basis", "other"] = "other"
    document_date: date | None = None
    effective_from: date | None = None
    status: Literal["draft", "unverified"] = "unverified"
    synthetic: bool = False
    external_policy: Literal["prohibited", "synthetic_allowed"] = "prohibited"

    @field_validator("title", "revision")
    @classmethod
    def clean_label(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("empty label")
        return value

    @model_validator(mode="after")
    def consistency(self):
        if self.external_policy == "synthetic_allowed" and not self.synthetic:
            raise ValueError("External processing is only available for explicitly synthetic data")
        if self.document_id is None and self.expected_version != 0:
            raise ValueError("A new document starts at version zero")
        return self


class JobView(BaseModel):
    id: str
    status: JobStatus
    version: int
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    message: str
    warnings: list[str]
    block_count: int
    fragment_count: int
    page_count: int | None
    parser_version: str
    can_retry: bool
    can_cancel: bool


class UploadResult(BaseModel):
    version_id: str
    document_id: str
    document_version: int
    job_id: str
    replayed: bool


class JobCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class VersionView(BaseModel):
    id: str
    revision: str
    title: str
    registered_at: datetime
    processing_status: str


class DocumentDetail(BaseModel):
    id: str
    department_id: str
    title: str
    revision: str
    status: str
    effective_from: date | None
    document_date: date | None
    registered_at: datetime
    synthetic: bool
    sha256: str
    document_id: str | None
    document_version: int | None
    filename: str | None
    format: str | None
    size: int | None
    document_kind: str | None
    external_policy: str
    job: JobView | None
    versions: list[VersionView]
    can_upload_revision: bool
    can_download: bool


class FragmentView(BaseModel):
    id: str
    document_version_id: str
    title: str
    revision: str
    department_id: str
    locator: str
    quote: str
    ordinal: int
    page: int | None
    kind: str


class TextContext(BaseModel):
    block_id: str
    text: str
    start: int
    end: int
    page: int | None
    kind: str


class DocumentOptions(BaseModel):
    writable_departments: list[str]
    max_file_bytes: int
    formats: list[Literal["docx", "pdf"]]
    max_pages: int
    ocr_available: bool
    external_calls_on_upload: bool
