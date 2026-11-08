"""Stage 3 contracts: a proposal never silently changes the official registry."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ChangeKind = Literal[
    "transfer_function",
    "responsibility",
    "split_function",
    "merge_function",
    "abolish_function",
    "add_seat",
    "close_seat",
    "position",
    "reporting",
    "temporary_substitution",
    "document",
]
ChangeState = Literal[
    "draft",
    "in_review",
    "returned",
    "rejected",
    "approved",
    "risk_accepted",
    "planned",
    "partially_executed",
    "executed",
    "verified",
    "cancelled",
]
ReviewOutcome = Literal["agree", "return", "reject", "request_info"]
DecisionOutcome = Literal["approve", "return", "reject", "accept_risk"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChangeItemInput(Input):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-zA-Z0-9_-]+$")
    kind: ChangeKind
    target_id: str | None = Field(default=None, max_length=36)
    before: str = Field(min_length=1, max_length=3000)
    after: str = Field(min_length=1, max_length=3000)
    source_anchor_ids: list[str] = Field(default_factory=list, max_length=20)
    requirements: str = Field(default="", max_length=2000)
    candidate_employee_id: str | None = Field(default=None, max_length=36)
    qualification_basis: str = Field(default="", max_length=2000)
    qualification_source_ids: list[str] = Field(default_factory=list, max_length=20)


class ChangeSectionInput(Input):
    department_id: str = Field(min_length=1, max_length=36)
    title: str = Field(min_length=3, max_length=180)
    rationale: str = Field(min_length=3, max_length=5000)
    items: list[ChangeItemInput] = Field(min_length=1, max_length=30)


class ProposalCreate(Input):
    title: str = Field(min_length=3, max_length=180)
    effective_date: date
    sections: list[ChangeSectionInput] = Field(min_length=1, max_length=12)


class ChangeCommand(Input):
    expected_version: int = Field(ge=1)


class ProposalRevision(ProposalCreate, ChangeCommand):
    change_reason: str = Field(min_length=3, max_length=2000)


class Recipient(Input):
    department_id: str
    user_id: str


class ProposalSend(ChangeCommand):
    recipients: list[Recipient] = Field(min_length=1, max_length=60)
    due_at: datetime
    message: str = Field(min_length=3, max_length=3000)

    @field_validator("due_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Укажите срок с часовым поясом")
        return value


class ProposalMessage(ChangeCommand):
    department_id: str
    text: str = Field(min_length=1, max_length=5000)
    source_anchor_ids: list[str] = Field(default_factory=list, max_length=20)


class ProposalReview(ProposalMessage):
    outcome: ReviewOutcome


class ProposalDecision(ChangeCommand):
    outcome: DecisionOutcome
    reason: str = Field(min_length=3, max_length=5000)
    source_anchor_ids: list[str] = Field(default_factory=list, max_length=20)


class ProposalExecution(ChangeCommand):
    department_id: str
    item_key: str = Field(min_length=1, max_length=60)
    status: Literal["planned", "completed"]
    effective_date: date
    note: str = Field(min_length=3, max_length=3000)
    source_anchor_id: str | None = None
    personnel_event_id: str | None = None


class ProposalVerify(ChangeCommand):
    note: str = Field(min_length=3, max_length=3000)


class ProposalReminder(ChangeCommand):
    recipient: Recipient
    kind: Literal["reminder", "escalation"]
    note: str = Field(min_length=3, max_length=2000)


class ProposalDeliveryRetry(ChangeCommand):
    notification_id: str = Field(min_length=1, max_length=36)


class Choice(BaseModel):
    id: str
    label: str


class ParticipantChoice(Choice):
    department_id: str
    roles: list[str]


class ChangeOptions(BaseModel):
    departments: list[Choice]
    author_departments: list[str]
    participants: list[ParticipantChoice]
    can_create: bool


class SourceChoice(Choice):
    document_version_id: str
    locator: str
    quote: str


class ChangeContext(BaseModel):
    sources: list[SourceChoice]
    functions: list[Choice]
    positions: list[Choice]
    seats: list[Choice]
    employees: list[Choice]
    personnel_events: list[Choice]


class ChangeItem(ChangeItemInput):
    impacts: list[str]


class ChangeSection(BaseModel):
    department_id: str
    department_name: str
    title: str
    rationale: str
    items: list[ChangeItem]
    can_review: bool
    can_execute: bool


class ChangeVersion(BaseModel):
    id: str
    number: int
    title: str
    effective_date: date
    change_reason: str
    created_at: datetime
    sections: list[ChangeSection]


class ChangeEntry(BaseModel):
    id: str
    kind: str
    version_number: int
    department_id: str | None
    actor_name: str
    actor_id: str
    created_at: datetime
    text: str
    outcome: str | None = None
    source_anchor_ids: list[str] = []
    item_key: str | None = None
    effective_date: date | None = None
    personnel_event_id: str | None = None
    recipient_id: str | None = None
    recipient_name: str | None = None
    due_at: datetime | None = None
    delivery_status: str | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    replied_at: datetime | None = None
    overdue: bool = False
    applicable: bool = False
    notification_id: str | None = None
    can_retry_delivery: bool = False


class ChangePermissions(BaseModel):
    revise: bool
    send: bool
    message: bool
    private_note: bool
    decide: bool
    verify: bool
    remind: bool
    cancel: bool


class ProposalSummary(BaseModel):
    id: str
    title: str
    state: ChangeState
    version: int
    current_version: int
    effective_date: date
    created_at: datetime
    updated_at: datetime
    departments: list[Choice]
    partial_visibility: bool
    overdue: bool


class ProposalDetail(ProposalSummary):
    owner_name: str
    versions: list[ChangeVersion]
    entries: list[ChangeEntry]
    process_entries: list[ChangeEntry]
    permissions: ChangePermissions
    limitations: list[str]
    history_total: int
    history_offset: int
    history_limit: int
    versions_total: int
    versions_offset: int
    versions_limit: int


class ChangeNotification(BaseModel):
    id: str
    proposal_id: str
    title: str
    kind: str
    department_id: str
    created_at: datetime
    delivered_at: datetime | None
    read_at: datetime | None
