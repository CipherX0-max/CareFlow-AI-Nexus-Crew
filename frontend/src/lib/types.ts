export type Role = "patient" | "staff" | "admin";
export type Queue = "URGENT" | "NORMAL" | "NEEDS_REVIEW";
export type CaseStatus = "draft" | "submitted" | "in_review" | "awaiting_patient" | "resolved" | "deletion_requested";

export interface User { id: string; email: string; display_name: string; role: Role; email_verified: boolean; must_change_password: boolean; is_synthetic: boolean }
export interface Me { user: User; csrf_token: string; consent: { granted: boolean; text_version: string | null }; profile: { birth_year: number | null; preferred_language: string } | null }

export interface PublicConfig {
  consent: { version: string; title: string; sections: { heading: string; body: string }[] };
  emergency: { message_en: string; message_hi: string; message_hi_Latn: string; number: string; clinician_reviewed: boolean };
  demonstration_rules: boolean; ruleset_version: string;
  limits: { max_upload_mb: number; max_audio_mb: number; max_audio_seconds: number; max_message_chars: number };
  languages_supported: string[]; development_mode: boolean;
  features: { llm: boolean; speech: boolean; ocr: boolean };
}

export interface EmergencyResult { triggered: boolean; concepts: string[]; language?: string; message?: Record<string, string>; clinician_reviewed?: boolean; note?: string }

export interface Message { id: string; seq: number; author_role: string; kind: string; text: string; language: string; language_support: string; text_en: string | null; translation_flags: string[]; question_id: string | null; created_at: string }
export interface Segment { start?: number; end?: number; text: string; confidence?: number; page?: number; method?: string; flags?: string[] }
export interface UploadItem {
  id: string; kind: "audio" | "pdf" | "image"; filename: string; mime: string; size_bytes: number; status: "pending" | "processing" | "needs_confirmation" | "confirmed" | "failed" | "discarded";
  error: string | null; method: string; detected_language: string | null; language_confidence: number | null; flags: string[]; mean_confidence: number | null; confirmed: boolean; created_at: string;
  extracted_text?: string; translated_text?: string | null; confirmed_text?: string | null; segments?: Segment[];
}
export interface QuestionOption { value: string; label: string }
export interface Question { id: string; key: string; text: string; reason: string; answer_type: "text" | "choice" | "number" | "yesno"; options: QuestionOption[]; concept: string | null; status: string; answer_text: string | null; answer_choice: string | null; asked_at: string; answered_at: string | null; conflict_id: string | null }
export interface Understood { id: string; label: string; polarity: string; temporal: string; category: string; detail: string; value: number | null; unit: string | null }
export interface PatientCase {
  id: string; case_number: string; status: CaseStatus; status_label: string; created_at: string; submitted_at: string | null; processing: boolean;
  messages: Message[]; uploads: UploadItem[]; questions: Question[]; understood: Understood[]; emergency_flagged: boolean; language: string; can_submit: boolean; deletion_requested_at: string | null;
}
export interface PatientCaseRow { id: string; case_number: string; status: CaseStatus; status_label: string; chief_complaint: string; created_at: string; submitted_at: string | null; open_questions: number }

export interface CaseRow {
  id: string; case_number: string; status: CaseStatus; queue: Queue | null; proposed_queue: Queue | null; confirmed_queue: Queue | null; reassessment_pending: boolean;
  chief_complaint: string; language: string; submitted_at: string | null; updated_at: string; waiting_minutes: number | null; assigned_to: string | null; assigned_label: string;
  patient_label: string; emergency_flagged: boolean; is_synthetic: boolean; sort_tier: number; escalation: number; processing_state: string;
  age: number | null; symptom_duration: string | null; worsening: boolean; vitals: Record<string, { value: number | null; value2: number | null; unit: string | null }>;
  reason: string; missing_count: number; conflict_count: number; sources: string[];
}
export interface QueueSummary { counts: Record<Queue, number>; total_open: number; total_cases: number; workflow_counts: Partial<Record<CaseStatus, number>>; unassigned: number; reassessment_pending: number; version: number; processing: number }

export interface Fact {
  id: string; category: string; concept: string; label: string; polarity: "present" | "denied" | "uncertain"; temporal: string; value: number | null; value2: number | null; value_text: string | null;
  unit: string | null; onset: string | null; duration: string | null; frequency: string | null; severity: string | null; progression: string | null; origin: string;
  source: { type: string; id: string; locator: string | null; speaker: string }; excerpt: string; stated_at: string | null; measured_at: string | null; confidence: number | null; method: string;
  verification: string; flags: string[]; superseded: boolean; conflict_ids: string[];
}
export interface Evidence { fact_id?: string; source_type?: string; locator?: string | null; excerpt?: string; polarity?: string; temporal?: string; stated_at?: string | null; reviewer_considered?: string[] }
export interface Conflict {
  id: string; kind: string; concept: string; safety_critical: boolean; description: string; status: "unresolved" | "clarified" | "reviewer_resolved"; clarification: string | null;
  resolution_text: string | null; resolved_by: string | null; resolved_at: string | null; evidence: Evidence[]; llm_opinion: { classification: string; rationale: string; model: string } | null; created_at: string; fact_ids: string[];
}
export interface TriggeredRule { rule_id: string; name: string; tier: number; rationale: string; placeholder_threshold: boolean; supporting_fact_ids: string[]; ruleset_version: string }
export interface Assessment {
  id: string; ruleset_version: string; demonstration_only: boolean; trigger: string; proposed_queue: Queue; summary: string; triggered_rules: TriggeredRule[]; supporting_fact_ids: string[];
  missing_information: { key: string; label: string; blocking?: boolean }[]; open_conflict_ids: string[]; data_quality: { code: string; message: string }[]; failure: string | null; changed_from: Queue | null; created_at: string;
  sort_tier: number; escalation: number;
}
export interface Decision { id: string; assessment_id: string; proposed_queue: Queue; final_queue: Queue; action: string; reason: string; decided_by: string; decided_at: string }
export interface Note { id: string; version: number; status: "draft" | "approved" | "rejected" | "superseded"; ai_generated: boolean; content: Record<string, any>; change_summary: any[]; edited_by: string | null; reviewed_by: string | null; reviewed_at: string | null; review_reason: string | null; created_at: string; updated_at: string; stale: boolean; label: string }
export interface AuditItem { id: number; ts: string; actor: string; role: string; action: string; details: Record<string, any> }
export interface CaseDetail {
  case: CaseRow & { confirmed_by: string | null; confirmed_at: string | null; created_at: string };
  messages: Message[]; uploads: UploadItem[]; facts: Fact[]; conflicts: Conflict[]; questions: Question[]; assessment: Assessment | null; assessment_history: Assessment[];
  decisions: Decision[]; notes: Note[]; audit: AuditItem[]; conflict_outcomes: Record<string, string[]>;
}
export interface AdminUser { id: string; email: string; display_name: string; role: Role; is_active: boolean; created_at: string; email_verified: boolean; is_synthetic: boolean; locked: boolean }
export interface SystemInfo {
  database: { available: boolean; detail: string }; llm: { available: boolean; model: string; detail: string }; speech: { available: boolean; model: string; detail: string };
  ocr: { available: boolean; detail: string; languages?: string[] };
  ruleset: { version: string; name: string; clinically_approved: boolean; note: string; urgent_rules: { id: string; name: string; tier: number; placeholder: boolean }[]; safety_critical_concepts: string[]; ordering_policy: string };
  knowledge: { available: boolean; detail?: string; version?: string; status?: string; purpose?: string; clinically_approved?: boolean; review_status?: string; source_organisation?: string | null; source_reference?: string | null; effective_date?: string | null; terminology?: { namespace: string; external_standard: string | null; snomed_ct: { status: string; licence_review: string; mapping_status: string } }; concept_count?: number; external_mappings?: number; protocol_version?: string; ruleset_version?: string; matches_active_ruleset?: boolean; rule_ids_match?: boolean; note?: string };
  limits: Record<string, number>; environment: string; expose_dev_tokens: boolean; cookie_secure: boolean; counts: Record<string, number>;
}
