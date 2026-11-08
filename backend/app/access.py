from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import audit
from app.access_models import GrantRevocation
from app.models import RoleAssignment, now

BUSINESS_ROLES = {"auditor", "hr", "department", "reviewer", "manager"}


def grants(db: Session, user_id: str, at: datetime | None = None) -> list[RoleAssignment]:
    moment = at or now()
    return list(
        db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user_id,
                RoleAssignment.valid_from <= moment,
                or_(RoleAssignment.valid_until.is_(None), RoleAssignment.valid_until > moment),
                ~select(GrantRevocation.id)
                .where(
                    GrantRevocation.assignment_id == RoleAssignment.id,
                    GrantRevocation.created_at <= moment,
                )
                .exists(),
            )
        )
    )


def departments(db: Session, user_id: str, role: str | None = None) -> set[str]:
    return {
        g.department_id
        for g in grants(db, user_id)
        if g.department_id and (g.role == role if role else g.role in BUSINESS_ROLES)
    }


def allowed(db: Session, user_id: str, department_id: str, permission: str = "read") -> bool:
    roles = {
        "read": BUSINESS_ROLES,
        "personnel.write": {"hr"},
        "personnel.private": {"hr"},
        "document.write": {"auditor", "hr", "department", "manager"},
    }.get(permission, set())
    return any(g.department_id == department_id and g.role in roles for g in grants(db, user_id))


def require(db: Session, user_id: str, department_id: str, permission: str = "read"):
    if not allowed(db, user_id, department_id, permission):
        # No denied object id or department names in the public error or safe log.
        audit.record(
            db, "access.denied", user_id, category="security", payload={"permission": permission}
        )
        db.commit()
        raise HTTPException(404, "Объект недоступен или не существует")
