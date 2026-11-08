from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.change_schemas import Choice


class ReportContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=3, max_length=180)
    objective: str = Field(default="", max_length=3000)
    scope: str = Field(default="", max_length=3000)
    period: str = Field(default="", max_length=300)
    criteria: str = Field(default="", max_length=3000)
    method: str = Field(default="", max_length=3000)
    limitations: str = Field(default="", max_length=3000)
    findings: str = Field(default="", max_length=5000)
    significance: str = Field(default="", max_length=3000)
    recommendations: str = Field(default="", max_length=5000)
    action_plan: str = Field(default="", max_length=5000)
    conclusion: str = Field(default="", max_length=5000)

    @field_validator("*")
    @classmethod
    def printable(cls, value):
        if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("Удалите управляющие символы из текста")
        return value


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    department_id: str | None = None
    reviewer_id: str
    recipients: list[str] = Field(min_length=1, max_length=40)
    previous_id: str | None = None


class ReportCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    note: str = Field(default="", max_length=3000)

    @field_validator("note")
    @classmethod
    def printable_note(cls, value):
        return ReportContent.printable(value)


class ReportSave(ReportCommand):
    content: ReportContent


class ReportReview(ReportCommand):
    outcome: Literal["accept", "return"]
    checked: bool


class ReportBlock(BaseModel):
    heading: str = ""
    text: str = ""
    source_id: str | None = None
    url: str | None = None


class ReportSnapshot(BaseModel):
    proposal_version: int
    captured_at: str
    blocks: list[ReportBlock]


class ReportSummary(BaseModel):
    id: str
    title: str
    state: str
    revision: int
    kind: str
    created_at: datetime


class ReportEvent(BaseModel):
    kind: str
    note: str
    revision: int
    actor: str
    created_at: datetime


class ReportDetail(ReportSummary):
    proposal_id: str
    department_ids: list[str]
    previous_id: str | None
    version: int
    content: ReportContent
    snapshot: ReportSnapshot
    digest: str
    owner: str
    reviewer: str
    recipients: list[str]
    can_edit: bool
    can_submit: bool
    can_review: bool
    can_release: bool
    can_correct: bool
    can_history: bool
    release: dict[str, str] | None
    events: list[ReportEvent]
    events_total: int


class ReportOptions(BaseModel):
    proposals: list[Choice]
    departments: list[Choice]
    reviewers: list[Choice]
    recipients: list[Choice]


class ReportRevisionView(BaseModel):
    number: int
    content: ReportContent
    snapshot: ReportSnapshot
    digest: str
    created_at: datetime


Content = ReportContent
Create = ReportCreate
Command = ReportCommand
Save = ReportSave
Review = ReportReview
Block = ReportBlock
Snapshot = ReportSnapshot
Summary = ReportSummary
Event = ReportEvent
Detail = ReportDetail
Options = ReportOptions
