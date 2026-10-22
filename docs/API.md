# CareFlow AI HTTP API

Generated from the FastAPI OpenAPI schema (`python -m backend.careflow.scripts.export_api_docs`). Interactive docs: `http://localhost:8000/docs`.

* Authentication: opaque session cookie `cf_session` (HttpOnly, SameSite=Lax). State-changing requests also need the header `X-CSRF-Token` (returned by `/api/auth/login`, `/api/auth/signup`, `/api/auth/me`).
* Errors always look like `{"error": {"code": "...", "message": "...", "fields": [...]}}`. Common codes: `not_authenticated` 401, `csrf_failed` 403, `forbidden` 403, `case_not_found` 404 (also returned for another patient's case), `consent_required` 403, `validation_error` 422, `stale_assessment` 409, `reason_required` 422, `decision_required` 409.
* Every route verifies identity, role and (for cases) ownership on the server. Admins cannot open case contents.

| Method | Path | Role | Summary |
|---|---|---|---|
| GET | `/api/admin/audit` | admin | Audit Log |
| GET | `/api/admin/audit/verify` | admin | Audit Verify |
| POST | `/api/admin/cases/{cid}/purge` | admin | Fulfil a patient's deletion request: removes content and files; keeps a tombstone case row and the audit trail. |
| GET | `/api/admin/deletion-requests` | admin | Deletion Requests |
| GET | `/api/admin/system` | admin | System |
| GET | `/api/admin/users` | admin | Users |
| POST | `/api/admin/users` | admin | Only an authorised administrator can create staff/admin accounts. A one-time temporary password is returned (no email service). |
| PATCH | `/api/admin/users/{uid}` | admin | Patch User |
| POST | `/api/auth/login` | public/any | Login |
| POST | `/api/auth/logout` | public/any | Logout |
| GET | `/api/auth/me` | public/any | Me |
| POST | `/api/auth/password/change` | public/any | Change |
| POST | `/api/auth/password/forgot` | public/any | Always answers the same way (no account-existence oracle). |
| POST | `/api/auth/password/reset` | public/any | Reset |
| POST | `/api/auth/signup` | public/any | Public sign-up ALWAYS creates a patient. Staff/admin accounts can only be created by an administrator. |
| POST | `/api/auth/verify-email` | public/any | Verify Email |
| GET | `/api/cases` | patient (own records only) | My Cases |
| POST | `/api/cases` | patient (own records only) | Create Case |
| GET | `/api/cases/{case_id}` | patient (own records only) | Get My Case |
| POST | `/api/cases/{case_id}/deletion-request` | patient (own records only) | Request Deletion |
| POST | `/api/cases/{case_id}/emergency-check` | patient (own records only) | Emergency Check |
| POST | `/api/cases/{case_id}/messages` | patient (own records only) | Add Message |
| POST | `/api/cases/{case_id}/questions/{qid}/answer` | patient (own records only) | Answer Question |
| POST | `/api/cases/{case_id}/submit` | patient (own records only) | Submit Case |
| POST | `/api/cases/{case_id}/uploads` | patient (own records only) | Upload File |
| GET | `/api/cases/{case_id}/uploads/{uid}` | patient (own records only) | Get Upload |
| POST | `/api/cases/{case_id}/uploads/{uid}/confirm` | patient (own records only) | Confirm Upload |
| POST | `/api/cases/{case_id}/uploads/{uid}/discard` | patient (own records only) | Discard Upload |
| GET | `/api/cases/{case_id}/uploads/{uid}/file` | patient (own records; staff may read via /api/cases/{id}/uploads/{uid}/file) | Download Upload |
| GET | `/api/config/public` | public | Public Config |
| GET | `/api/health` | public | Health |
| POST | `/api/patient/consent` | patient (own records only) | Give Consent |
| POST | `/api/patient/consent/withdraw` | patient (own records only) | Withdraw Consent |
| PUT | `/api/patient/profile` | patient (own records only) | Update Profile |
| GET | `/api/staff/cases` | staff | Deterministic ordering: rule tier asc, worsening indicator desc, longest-waiting first (submitted_at asc), then case id. |
| GET | `/api/staff/cases/{case_id}` | staff | Get Case |
| POST | `/api/staff/cases/{case_id}/assign` | staff | Assign |
| POST | `/api/staff/cases/{case_id}/conflicts/{cid}/resolve` | staff | Resolve Conflict |
| POST | `/api/staff/cases/{case_id}/decision` | staff | Decide |
| POST | `/api/staff/cases/{case_id}/facts/{fid}/correct` | staff | Append a staff-verified correction and supersede the extraction; the original source fact remains in the record. |
| POST | `/api/staff/cases/{case_id}/facts/{fid}/verify` | staff | Verify Fact |
| POST | `/api/staff/cases/{case_id}/messages/{mid}/acknowledge-flags` | staff | Ack Flags |
| POST | `/api/staff/cases/{case_id}/notes/draft` | staff | Draft Note |
| PUT | `/api/staff/cases/{case_id}/notes/{nid}` | staff | Edit Note |
| POST | `/api/staff/cases/{case_id}/notes/{nid}/approve` | staff | Approve Note |
| POST | `/api/staff/cases/{case_id}/notes/{nid}/reject` | staff | Reject Note |
| GET | `/api/staff/cases/{case_id}/notes/{nid}/text` | staff | Note Text |
| POST | `/api/staff/cases/{case_id}/questions` | staff | Ask Patient |
| POST | `/api/staff/cases/{case_id}/reassess` | staff | Reassess |
| POST | `/api/staff/cases/{case_id}/status` | staff | Set Status |
| POST | `/api/staff/cases/{case_id}/uploads/{uid}/acknowledge-flags` | staff | Ack Upload Flags |
| GET | `/api/staff/queues/summary` | staff | Summary |
| GET | `/api/staff/reviewers` | staff | Reviewers |
