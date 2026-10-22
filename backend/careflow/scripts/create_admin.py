"""Create (or reset) an administrator account. The only way to bootstrap the first admin; there are no default credentials.

    python -m backend.careflow.scripts.create_admin --email you@org.example --name "Your Name"
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from ..db import session_scope
from ..main import init_database
from ..models import User, utcnow
from ..security import hash_password, password_problem


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", choices=["admin", "staff"], default="admin")
    a = ap.parse_args()
    pw = getpass.getpass("Password (min 10 chars): ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    if (p := password_problem(pw)):
        sys.exit(p)
    init_database()
    with session_scope() as db:
        u = db.execute(select(User).where(User.email == a.email.lower())).scalar_one_or_none()
        if u:
            u.password_hash, u.role, u.is_active = hash_password(pw), a.role, True
            print(f"Updated existing account {u.email} ({u.role}).")
        else:
            db.add(User(email=a.email.lower(), display_name=a.name, role=a.role, password_hash=hash_password(pw), email_verified_at=utcnow()))
            print(f"Created {a.role} account {a.email.lower()}.")


if __name__ == "__main__":
    main()
