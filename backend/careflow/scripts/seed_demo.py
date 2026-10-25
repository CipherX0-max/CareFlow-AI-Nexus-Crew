"""Seed SYNTHETIC demo users and cases (clearly marked is_synthetic) by driving the real API in-process.

    python -m backend.careflow.scripts.seed_demo [--only S01,S10,S19] [--no-llm]

Creates 1 admin, 2 staff and 3 patients with RANDOM passwords (printed once, saved to data/demo_credentials.txt, mode 600),
then submits the curated scenarios so each queue has examples. Safe to re-run: existing demo users are reused."""
import argparse
import os
import secrets
import tempfile
from pathlib import Path

from sqlalchemy import select

from ..config import ROOT, get_settings
from ..db import session_scope
from ..main import app, init_database
from ..models import Consent, PatientProfile, User, utcnow
from ..content import CONSENT_VERSION
from ..security import hash_password

DEFAULT = ["S01-english", "S03-hinglish", "S10-present-then-denied", "S11-resolved", "S12-text-vs-report", "S14-missing", "S16-urgent-demo-rule", "S09-present", "S17-cannot-categorise", "S19-full-multimodal"]
USERS = [("admin.demo@careflow-demo.org", "Demo Admin", "admin"), ("dr.asha@careflow-demo.org", "Dr Asha Demo", "doctor"), ("dr.ravi@careflow-demo.org", "Dr Ravi Demo", "doctor"), ("ops.meera@careflow-demo.org", "Meera (Intake Staff) Demo", "staff"),
         ("patient.one@careflow-demo.org", "Synthetic Patient One", "patient"), ("patient.two@careflow-demo.org", "Synthetic Patient Two", "patient"), ("patient.three@careflow-demo.org", "Synthetic Patient Three", "patient")]


def ensure_users() -> dict[str, str]:
    creds, lines = {}, []
    with session_scope() as db:
        for email, name, role in USERS:
            u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if u is None:
                pw = secrets.token_urlsafe(10) + "aA1!"
                u = User(email=email, display_name=name, role=role, password_hash=hash_password(pw), is_synthetic=True, email_verified_at=utcnow())
                db.add(u)
                db.flush()
                if role == "patient":
                    db.add(PatientProfile(user_id=u.id))
                    db.add(Consent(user_id=u.id, text_version=CONSENT_VERSION))
                creds[email] = pw
                lines.append(f"{role:8} {email}  {pw}")
    if lines:
        p = ROOT / "data" / "demo_credentials.txt"
        p.write_text("\n".join(lines) + "\n")
        os.chmod(p, 0o600)
    return creds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    if a.no_llm:
        os.environ["LLM_ENABLED"] = "false"
        get_settings.cache_clear()
    init_database()
    creds = ensure_users()
    if not creds:
        print("Demo users already exist; passwords were printed when first created (data/demo_credentials.txt).")
        pw_file = ROOT / "data" / "demo_credentials.txt"
        creds = {l.split()[1]: l.split()[2] for l in pw_file.read_text().splitlines()} if pw_file.exists() else {}
    else:
        print("Created synthetic demo accounts (shown once; also saved to data/demo_credentials.txt):")
        for e, p in creds.items():
            print(f"  {e}  {p}")
    from ..demo.scenarios import SCENARIOS, finish_review, run_scenario
    from ..tests.helpers import Api
    from ..services import stt
    from ..tests import media
    want = [s for s in a.only.split(",") if s] or DEFAULT
    chosen = [s for s in SCENARIOS if s.id in want]
    patients = [e for e, _, r in USERS if r == "patient"]
    from fastapi.testclient import TestClient
    with TestClient(app):
        staff = Api(app)
        if staff.login("dr.asha@careflow-demo.org", creds["dr.asha@careflow-demo.org"]).status_code != 200:
            raise SystemExit("Could not sign in as demo staff; delete data/careflow.db and data/demo_credentials.txt to re-seed from scratch.")
        tmp = Path(tempfile.mkdtemp())
        for i, scn in enumerate(chosen):
            if ("tts" in scn.needs and not media.have_tts()) or ("whisper" in scn.needs and not stt.status()["available"]):
                print(f"skip {scn.id}: needs speech tooling")
                continue
            p = Api(app)
            email = patients[i % len(patients)]
            assert p.login(email, creds[email]).status_code == 200
            out = run_scenario(p, staff, scn, tmp)
            d = out["detail"]
            print(f"{scn.id:28} -> {d['assessment']['proposed_queue']:13} {d['case']['case_number']}  {scn.title}")
        s = staff.get("/api/staff/queues/summary").json()
        print("Queues:", s["counts"], "- sign in as dr.asha@careflow-demo.org to review (leave cases undecided so the dashboard has work).")


if __name__ == "__main__":
    main()
