from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import Page

Category = Literal[
    "unchanged",
    "possible_rephrasing",
    "possible_transfer",
    "possible_duplication",
    "split",
    "merge",
    "changed_content",
    "new_text",
    "documented_abolition",
    "not_found",
    "conflict",
    "insufficient_data",
]


class CorpusCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=180)
    as_of: date
    version_ids: list[str] = Field(min_length=1, max_length=20)
    declared_complete: bool = False

    @field_validator("version_ids")
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value) or any(len(v) != 36 for v in value):
            raise ValueError("Duplicate or invalid versions")
        return sorted(value)


class CorpusDocument(BaseModel):
    id: str
    title: str
    revision: str
    department_id: str
    status: str
    effective_from: date | None
    sha256: str
    processing_status: str
    parser_version: str | None
    anchor_count: int
    warnings: list[str]


class CorpusView(BaseModel):
    id: str
    title: str
    as_of: date
    declared_complete: bool
    created_at: datetime
    fragment_count: int
    digest: str
    limitations: list[str]
    documents: list[CorpusDocument]


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=180)
    before_id: str = Field(min_length=36, max_length=36)
    after_id: str = Field(min_length=36, max_length=36)
    criteria: str = Field(min_length=10, max_length=2000)


class AnalysisJobView(BaseModel):
    id: str
    kind: str
    status: str
    version: int
    attempts: int
    created_at: datetime
    finished_at: datetime | None
    error_code: str | None
    message: str
    result_summary: dict
    can_retry: bool
    can_cancel: bool


class RegistryLink(BaseModel):
    id: str
    title: str
    source_anchor_id: str
    active_before: bool
    active_after: bool


class RegistryCheck(BaseModel):
    before_count: int
    after_count: int
    declared_complete: bool
    linked_functions: list[RegistryLink]
    limitation: str


class ComparisonView(BaseModel):
    id: str
    title: str
    criteria: str
    created_at: datetime
    before: CorpusView
    after: CorpusView
    algorithm_version: str
    index_key: str
    limitations: list[str]
    job: AnalysisJobView
    can_review: bool
    registry_check: RegistryCheck


class Evidence(BaseModel):
    anchor_id: str
    version_id: str
    title: str
    revision: str
    department_id: str
    locator: str
    quote: str


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=0)
    decision: Literal["confirmed", "dismissed", "needs_evidence"]
    category: Category
    note: str = Field(min_length=10, max_length=2000)


class ReviewView(BaseModel):
    id: str
    version: int
    decision: str
    category: str
    note: str
    author: str
    created_at: datetime


class DiffPart(BaseModel):
    kind: Literal["equal", "delete", "insert"]
    text: str


class FindingView(BaseModel):
    id: str
    ordinal: int
    category: Category
    title: str
    explanation: str
    provenance: str
    method: str
    limitations: list[str]
    before: list[Evidence]
    after: list[Evidence]
    diff: list[DiffPart]
    review: ReviewView | None


class SearchHit(Evidence):
    score: float
    lexical_rank: int | None
    semantic_rank: int | None
    semantic_excerpt: str | None
    processing_status: str


class SearchResponse(Page[SearchHit]):
    mode_requested: str
    mode_used: str
    index_key: str
    available_fragments: int
    indexed_fragments: int
    warnings: list[str]


class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_ids: list[str] = Field(min_length=1, max_length=20)

    @field_validator("version_ids")
    @classmethod
    def distinct(cls, value):
        return CorpusCreate.distinct(value)


class SearchStatus(BaseModel):
    index_key: str
    model_id: str
    revision: str
    dimension: int
    local_model_available: bool
    available_fragments: int
    indexed_fragments: int
    pending_version_ids: list[str]
    active_jobs: list[AnalysisJobView]
    writable_departments: list[str]
