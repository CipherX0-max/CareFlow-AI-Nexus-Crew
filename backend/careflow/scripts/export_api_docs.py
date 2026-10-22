"""Generate docs/API.md from the live OpenAPI schema so the docs cannot drift from the code.

    python -m backend.careflow.scripts.export_api_docs
"""
from pathlib import Path

from ..config import ROOT
from ..main import app

ROLE_HINT = (("/api/admin", "admin"), ("/api/staff", "staff"), ("/api/auth", "public/any"), ("/api/health", "public"), ("/api/config/public", "public"))


def role_for(path: str) -> str:
    for prefix, role in ROLE_HINT:
        if path.startswith(prefix):
            return role
    return "patient (own records; staff may read via /api/cases/{id}/uploads/{uid}/file)" if "/uploads/" in path and path.endswith("/file") else "patient (own records only)"


def main() -> None:
    schema = app.openapi()
    lines = ["# CareFlow AI HTTP API", "",
             "Generated from the FastAPI OpenAPI schema (`python -m backend.careflow.scripts.export_api_docs`). Interactive docs: `http://localhost:8000/docs`.", "",
             "* Authentication: opaque session cookie `cf_session` (HttpOnly, SameSite=Lax). State-changing requests also need the header `X-CSRF-Token` (returned by `/api/auth/login`, `/api/auth/signup`, `/api/auth/me`).",
             "* Errors always look like `{\"error\": {\"code\": \"...\", \"message\": \"...\", \"fields\": [...]}}`. Common codes: `not_authenticated` 401, `csrf_failed` 403, `forbidden` 403, `case_not_found` 404 (also returned for another patient's case), `consent_required` 403, `validation_error` 422, `stale_assessment` 409, `reason_required` 422, `decision_required` 409.",
             "* Every route verifies identity, role and (for cases) ownership on the server. Admins cannot open case contents.", "",
             "| Method | Path | Role | Summary |", "|---|---|---|---|"]
    for path, ops in sorted(schema["paths"].items()):
        for method, op in ops.items():
            summary = (op.get("summary") or "").replace("|", "/")
            doc = ((op.get("description") or "").strip().split("\n")[0]).replace("|", "/")
            lines.append(f"| {method.upper()} | `{path}` | {role_for(path)} | {doc or summary} |")
    out = ROOT / "docs" / "API.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
