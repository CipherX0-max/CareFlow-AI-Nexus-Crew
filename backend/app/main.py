"""FastAPI Entrypoint for CareFlow AI Backend with RBAC, Provider Orchestration, and Triage API."""
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel
from backend.app.config import get_settings
from backend.app.adapters.providers import ProviderOrchestrator, ProviderError
from backend.app.engine.urgency_scorer import ExplainableUrgencyEngine
from backend.app.services.case_service import CareFlowRepository

app = FastAPI(
    title="CareFlow AI — Human-in-the-Loop Clinical Triage API",
    version="1.0.0",
    description="Explainable healthcare intake, multilingual structuring, and deterministic urgency scoring.",
)

repo = CareFlowRepository()
orchestrator = ProviderOrchestrator(get_settings())
scorer = ExplainableUrgencyEngine()

# Seeded Demo Staff Tokens (Never grant access merely by selecting a UI role)
DEMO_STAFF_SESSIONS = {
    "demo-token-clinician-mehta": {
        "user_id": "staff-dr-mehta",
        "name": "Dr. Ananya Mehta, MD",
        "role": "clinician",
        "department": "Emergency & Acute Triage",
    },
    "demo-token-nurse-rao": {
        "user_id": "staff-nurse-rao",
        "name": "Sister Kavita Rao, RN",
        "role": "triage_nurse",
        "department": "Outpatient Intake Desk",
    },
    "demo-token-admin-kulkarni": {
        "user_id": "staff-admin-kulkarni",
        "name": "Rohan Kulkarni",
        "role": "admin",
        "department": "Clinical Operations",
    },
}


def require_staff_auth(authorization: Optional[str] = Header(default=None)) -> Dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Staff authentication required. Choosing 'Hospital Staff / Admin' on the landing screen does not grant access.",
        )
    token = authorization.replace("Bearer ", "").strip()
    staff = DEMO_STAFF_SESSIONS.get(token)
    if not staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired staff session token.")
    return staff


class StaffLoginRequest(BaseModel):
    email: str
    password: str


class ConsentPayload(BaseModel):
    patient_id: str
    age: Optional[int] = None
    preferred_language: str = "en-IN"
    consent_granted: bool


class CaseSubmissionPayload(BaseModel):
    case_id: str
    patient_id: str
    patient_text: str
    reported_severity: Optional[int] = None
    progression: str = "unknown"
    functional_impact: Optional[str] = None
    history: List[str] = []
    medications: List[str] = []
    patient_age: Optional[int] = None


class PriorityOverridePayload(BaseModel):
    new_priority_band: str
    reason: str


class FinalApprovalPayload(BaseModel):
    final_note: str


@app.get("/api/health/providers")
def provider_health():
    return orchestrator.get_health_report()


@app.post("/api/auth/staff-login")
def staff_login(payload: StaffLoginRequest):
    credentials_map = {
        ("dr.mehta@careflow.demo", "TriageDemo2026!"): "demo-token-clinician-mehta",
        ("nurse.rao@careflow.demo", "NurseDemo2026!"): "demo-token-nurse-rao",
        ("admin@careflow.demo", "AdminDemo2026!"): "demo-token-admin-kulkarni",
    }
    token = credentials_map.get((payload.email.strip().lower(), payload.password))
    if not token:
        raise HTTPException(status_code=401, detail="Invalid demo staff credentials.")
    user = DEMO_STAFF_SESSIONS[token]
    repo.record_audit(user["user_id"], user["role"], "STAFF_LOGIN_SUCCESS", {"email": payload.email})
    return {"access_token": token, "token_type": "bearer", "staff_profile": user, "is_demo_auth": True}


@app.post("/api/patient/consent")
def submit_consent(payload: ConsentPayload):
    return repo.record_consent(payload.patient_id, payload.age, payload.preferred_language, payload.consent_granted)


@app.post("/api/patient/consent/withdraw/{patient_id}")
def withdraw_consent(patient_id: str, reason: str = "User requested withdrawal"):
    return repo.withdraw_consent_and_request_deletion(patient_id, reason)


@app.post("/api/cases")
def create_and_score_case(payload: CaseSubmissionPayload):
    structuring_adapter = orchestrator.structuring_registry["demo_mock"]
    extraction = structuring_adapter.structure_case(
        patient_text=payload.patient_text,
        patient_metadata={
            "reported_severity": payload.reported_severity,
            "progression": payload.progression,
            "functional_impact": payload.functional_impact,
            "history": payload.history,
            "medications": payload.medications,
        },
    )
    score = scorer.evaluate_case(extraction, patient_age=payload.patient_age)
    try:
        return repo.create_case(payload.case_id, payload.patient_id, extraction, score)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


@app.get("/api/staff/queue")
def get_prioritized_queue(authorization: Optional[str] = Header(default=None)):
    require_staff_auth(authorization)
    return {"cases": repo.list_prioritized_queue()}


@app.post("/api/staff/cases/{case_id}/override")
def staff_override_priority(case_id: str, payload: PriorityOverridePayload, authorization: Optional[str] = Header(default=None)):
    staff = require_staff_auth(authorization)
    try:
        return repo.override_priority(case_id, staff["user_id"], staff["role"], payload.new_priority_band, payload.reason)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/staff/cases/{case_id}/approve")
def staff_approve_case(case_id: str, payload: FinalApprovalPayload, authorization: Optional[str] = Header(default=None)):
    staff = require_staff_auth(authorization)
    try:
        return repo.approve_final_note(case_id, staff["user_id"], staff["role"], payload.final_note)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/staff/audit")
def get_audit_events(case_id: Optional[str] = None, authorization: Optional[str] = Header(default=None)):
    require_staff_auth(authorization)
    return {"events": repo.get_audit_trail(case_id)}
