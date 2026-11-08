import argparse
import json
import secrets
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from app import audit
from app.auth import digest
from app.config import settings
from app.db import SessionLocal
from app.models import BrowserSession, RecoveryToken, UserAccount, now
from app.seed import seed, serialize_credentials
from app.seed_expansion import expand_demo


def main():
    parser = argparse.ArgumentParser(description="Локальные операции владельца OrgProekt")
    parser.add_argument(
        "command",
        choices=[
            "seed",
            "expand-demo",
            "workflow-demo",
            "reset-token",
            "block",
            "verify-audit",
            "seal-audit",
        ],
    )
    parser.add_argument("--username")
    parser.add_argument("--output", default="/private")
    args = parser.parse_args()
    private = Path(args.output)
    with SessionLocal() as db:
        if args.command == "seed":
            if settings().app_mode != "development":
                raise SystemExit("Демонстрационный seed доступен только в development")
            private.mkdir(parents=True, exist_ok=True)
            path = private / "demo-accounts.json"
            if path.exists():
                raise SystemExit("Файл учётных данных уже существует; повторный seed запрещён")
            path.write_text(serialize_credentials(seed(db)), encoding="utf-8")
            path.chmod(0o600)
            print(
                "Созданы синтетические записи. Пароли сохранены в .local/demo-accounts.json. Режим входа указан на странице входа."
            )
        elif args.command == "expand-demo":
            print(json.dumps(expand_demo(db), ensure_ascii=False))
        elif args.command == "workflow-demo":
            from app.workflow_accounts import provision

            provision(db, private, username=args.username)
            print(
                "Учебные аккаунты созданы. Состав и пароли — .local/workflow-accounts.json. Режим входа указан на сайте. Существующие аккаунты и права не изменены."
            )
        elif args.command in {"reset-token", "block"}:
            user = db.scalar(
                select(UserAccount).where(UserAccount.username == args.username).with_for_update()
            )
            if not user:
                raise SystemExit("Учётная запись не найдена")
            if args.command == "block":
                user.active = False
                db.execute(
                    update(BrowserSession)
                    .where(BrowserSession.user_id == user.id)
                    .values(revoked=True)
                )
                audit.record(
                    db,
                    "access.account_blocked",
                    None,
                    category="security",
                    object_id=user.id,
                    payload={"executor": "host_operator"},
                )
            else:
                if not user.active:
                    raise SystemExit("Сброс пароля не разблокирует учётную запись")
                value = secrets.token_urlsafe(32)
                db.add(
                    RecoveryToken(
                        user_id=user.id,
                        token_hash=digest(value),
                        purpose="password",
                        expires_at=now() + timedelta(minutes=20),
                    )
                )
                private.mkdir(parents=True, exist_ok=True)
                path = private / "password-reset.txt"
                path.write_text(value, encoding="utf-8")
                path.chmod(0o600)
                audit.record(
                    db,
                    "auth.recovery_issued",
                    None,
                    category="security",
                    object_id=user.id,
                    payload={"executor": "host_operator"},
                )
                print(
                    "Одноразовый токен на 20 минут сохранён в .local/password-reset.txt. Передайте владельцу защищённым способом."
                )
            db.commit()
        else:
            result = audit.verify_chain(db)
            if args.command == "seal-audit":
                private.mkdir(parents=True, exist_ok=True)
                (private / "audit-seal.json").write_text(json.dumps(result), encoding="utf-8")
                print(
                    "Контрольная точка записана. Храните отдельную защищённую копию за пределами БД."
                )
            else:
                seal = private / "audit-seal.json"
                if seal.exists():
                    known = json.loads(seal.read_text())
                    if result.get("count", 0) < known.get("count", 0):
                        result = {"valid": False, "reason": "audit_tail_removed"}
                print(json.dumps(result))
            if not result["valid"]:
                raise SystemExit(1)


if __name__ == "__main__":
    main()
