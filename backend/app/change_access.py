"""Default-deny, current-grant and version-participation checks for change cases."""

from fastapi import HTTPException
from sqlalchemy import and_, exists, or_, select

from app import access, audit
from app.change_models import ChangeEntry, ChangeProposal, ChangeSection, ChangeVersion
from app.models import UserAccount


def roles(db, user_id, department_id):
    return {g.role for g in access.grants(db, user_id) if g.department_id == department_id}


def active(db, user_id):
    user = db.get(UserAccount, user_id)
    return bool(user and user.active)


def deny(db, actor_id):
    audit.record(
        db, "access.denied", actor_id, category="security", payload={"permission": "change.case"}
    )
    db.commit()
    raise HTTPException(404, "Объект недоступен или не существует")


def scoped_sections(db, user_id, proposal, version):
    scopes = access.departments(db, user_id)
    if not active(db, user_id) or not scopes:
        return []
    invitations = select(ChangeEntry.department_id).where(
        ChangeEntry.version_id == version.id,
        ChangeEntry.recipient_id == user_id,
        ChangeEntry.kind.in_(["send", "escalation"]),
    )
    own = access.departments(db, user_id, "auditor") if proposal.owner_id == user_id else set()
    return list(
        db.scalars(
            select(ChangeSection)
            .where(
                ChangeSection.version_id == version.id,
                ChangeSection.department_id.in_(scopes),
                or_(
                    ChangeSection.department_id.in_(own),
                    ChangeSection.department_id.in_(invitations),
                ),
            )
            .order_by(ChangeSection.department_id)
        )
    )


def visible_query(db, user_id):
    scopes = access.departments(db, user_id)
    own = access.departments(db, user_id, "auditor")
    invited = exists(
        select(ChangeEntry.id).where(
            ChangeEntry.version_id == ChangeVersion.id,
            ChangeEntry.department_id == ChangeSection.department_id,
            ChangeEntry.recipient_id == user_id,
            ChangeEntry.kind.in_(["send", "escalation"]),
        )
    ).correlate(ChangeVersion, ChangeSection)
    visible = exists(
        select(ChangeSection.id)
        .join(ChangeVersion)
        .where(
            ChangeVersion.proposal_id == ChangeProposal.id,
            ChangeSection.department_id.in_(scopes),
            or_(
                and_(ChangeProposal.owner_id == user_id, ChangeSection.department_id.in_(own)),
                invited,
            ),
        )
    ).correlate(ChangeProposal)
    return select(ChangeProposal).where(visible)


def current(db, proposal):
    return db.scalar(
        select(ChangeVersion).where(
            ChangeVersion.proposal_id == proposal.id,
            ChangeVersion.number == proposal.current_version,
        )
    )


def load(db, user_id, proposal_id, lock=False):
    query = select(ChangeProposal).where(ChangeProposal.id == proposal_id)
    proposal = db.scalar(query.with_for_update() if lock else query)
    if not proposal:
        deny(db, user_id)
    for version in db.scalars(
        select(ChangeVersion)
        .where(ChangeVersion.proposal_id == proposal.id)
        .order_by(ChangeVersion.number.desc())
    ):
        sections = scoped_sections(db, user_id, proposal, version)
        if sections:
            return proposal, version, sections
    deny(db, user_id)


def all_departments(db, version):
    return set(
        db.scalars(
            select(ChangeSection.department_id).where(ChangeSection.version_id == version.id)
        )
    )


def author(db, user_id, proposal, version):
    return proposal.owner_id == user_id and all_departments(db, version) <= access.departments(
        db, user_id, "auditor"
    )


def manager(db, user_id, proposal, version):
    scopes = all_departments(db, version)
    visible = {s.department_id for s in scoped_sections(db, user_id, proposal, version)}
    return (
        proposal.owner_id != user_id
        and scopes <= access.departments(db, user_id, "manager")
        and scopes <= visible
    )


def invited(db, user_id, version_id, department_id):
    return (
        db.scalar(
            select(ChangeEntry.id)
            .where(
                ChangeEntry.version_id == version_id,
                ChangeEntry.department_id == department_id,
                ChangeEntry.recipient_id == user_id,
                ChangeEntry.kind == "send",
            )
            .limit(1)
        )
        is not None
    )
