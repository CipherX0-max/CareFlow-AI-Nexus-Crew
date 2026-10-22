import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({ default: ({ href, children, ...p }: any) => <a href={href} {...p}>{children}</a> }));

import { CaseRows, Pager } from "@/components/QueueList";
import { Composer, EmergencyBanner, QuestionCard, UploadCard } from "@/components/patient";
import { DecisionPanel, actionFor } from "@/components/review";
import { setCsrf } from "@/lib/api";
import type { CaseDetail, CaseRow, Question, UploadItem } from "@/lib/types";

// The real API client runs against a mocked fetch, so error mapping is exercised too.
const fetchMock = vi.fn();
beforeEach(() => { vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); setCsrf("csrf-test"); });
const ok = (body: unknown = {}) => fetchMock.mockImplementation(async () => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } }));
const fail = (status: number, code: string, message: string) =>
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ error: { code, message } }), { status, headers: { "content-type": "application/json" } }));
const calls = (suffix: string) => fetchMock.mock.calls.filter((c) => String(c[0]).endsWith(suffix)).map((c) => ({ url: String(c[0]), init: c[1], body: c[1]?.body ? JSON.parse(c[1].body) : undefined }));

const row = (o: Partial<CaseRow> = {}): CaseRow => ({
  id: "c1", case_number: "CF-AAAA1111", status: "submitted", queue: "URGENT", proposed_queue: "URGENT", confirmed_queue: null, reassessment_pending: false, chief_complaint: "Chest pain for two days",
  language: "en", submitted_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", waiting_minutes: 75, assigned_to: null, assigned_label: "", patient_label: "Synthetic Patient",
  emergency_flagged: true, is_synthetic: true, sort_tier: 1, escalation: 0, processing_state: "idle", ...o,
  age: 40, symptom_duration: "2 days", worsening: false, vitals: {}, reason: "Demonstration rule matched.", missing_count: 0, conflict_count: 0, sources: ["text"], ...o,
});

describe("queue list states", () => {
  it("shows skeleton while loading", () => {
    render(<CaseRows rows={null} loading error={null} onRetry={() => {}} />);
    expect(screen.getByLabelText("Loading cases")).toBeInTheDocument();
  });
  it("shows an error with a working retry when nothing is cached", async () => {
    const retry = vi.fn();
    render(<CaseRows rows={null} loading={false} error={new Error("Cannot reach the server")} onRetry={retry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the server");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
  });
  it("shows an empty state", () => {
    render(<CaseRows rows={[]} loading={false} error={null} onRetry={() => {}} emptyHint="No urgent cases right now." />);
    expect(screen.getByText("No cases here")).toBeInTheDocument();
    expect(screen.getByText("No urgent cases right now.")).toBeInTheDocument();
  });
  it("renders rows with queue, waiting time, and safety markers", () => {
    render(<CaseRows rows={[row(), row({ id: "c2", case_number: "CF-BBBB2222", queue: "NEEDS_REVIEW", reassessment_pending: true, emergency_flagged: false })]} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("Urgent")).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getAllByText("1 h 15 min").length).toBe(2);
    expect(screen.getByLabelText("Changed since decision")).toBeInTheDocument();
    expect(screen.getAllByText("synthetic").length).toBe(2);
    expect(screen.getAllByRole("link")[0]).toHaveAttribute("href", "/staff/cases/c1");
  });
  it("keeps stale rows visible when a refresh fails (reconnecting)", () => {
    render(<CaseRows rows={[row()]} loading={false} error={new Error("net")} onRetry={() => {}} />);
    expect(screen.getByText("CF-AAAA1111")).toBeInTheDocument();
  });
  it("pager reports totals and disables edges", () => {
    const go = vi.fn();
    render(<Pager page={1} pageSize={10} total={25} onPage={go} />);
    expect(screen.getByText(/25 cases · page 1 of 3/)).toBeInTheDocument();
    expect(screen.getByLabelText("Previous page")).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Next page"));
    expect(go).toHaveBeenCalledWith(2);
  });
});

describe("emergency banner", () => {
  const msg = { triggered: true, concepts: ["chest_pain"], message: { en: "Call your local emergency number now.", hi: "तुरंत आपातकालीन नंबर पर कॉल करें।", "hi-Latn": "Turant call karein." }, note: "Not clinician reviewed" };
  it("renders nothing when not triggered", () => {
    const { container } = render(<EmergencyBanner result={{ triggered: false, concepts: [] }} lang="en" config={null} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("shows the localised message, the configured number and the review caveat", () => {
    render(<EmergencyBanner result={msg} lang="hi" config={{ emergency: { number: "112" } } as any} />);
    expect(screen.getByRole("alert")).toHaveTextContent("तुरंत आपातकालीन नंबर");
    expect(screen.getByRole("alert")).toHaveTextContent("Emergency number: 112");
    expect(screen.getByRole("alert")).toHaveTextContent("Not clinician reviewed");
  });
});

describe("composer", () => {
  it("sends text with an idempotency token and clears on success", async () => {
    ok({ emergency: { triggered: false } });
    const sent = vi.fn();
    render(<Composer caseId="c1" maxChars={5000} onSent={sent} onEmergency={() => {}} />);
    await userEvent.type(screen.getByLabelText("Describe how you feel"), "I have a cough");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sent).toHaveBeenCalled());
    const [call] = calls("/messages");
    expect(call.init.headers["X-CSRF-Token"]).toBe("csrf-test");
    expect(call.body.text).toBe("I have a cough");
    expect(call.body.client_token).toBeTruthy();
    expect(screen.getByLabelText("Describe how you feel")).toHaveValue("");
  });
  it("keeps the text and explains when sending fails", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    render(<Composer caseId="c1" maxChars={5000} onSent={() => {}} onEmergency={() => {}} />);
    await userEvent.type(screen.getByLabelText("Describe how you feel"), "headache");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the server");
    expect(screen.getByLabelText("Describe how you feel")).toHaveValue("headache");
  });
  it("shows the emergency banner trigger from the server response", async () => {
    ok({ emergency: { triggered: true, concepts: ["chest_pain"], message: { en: "x" } } });
    const em = vi.fn();
    render(<Composer caseId="c1" maxChars={5000} onSent={() => {}} onEmergency={em} />);
    await userEvent.type(screen.getByLabelText("Describe how you feel"), "chest pain");
    await userEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(em).toHaveBeenCalled());
  });
});

describe("follow-up question card", () => {
  const q: Question = { id: "q1", key: "conflict:abc", text: "Do you have chest pain right now?", reason: "We received different information about chest pain.", answer_type: "choice", status: "open", concept: "chest_pain", answer_text: null, answer_choice: null, asked_at: "", answered_at: null, conflict_id: "abc",
    options: [{ value: "present_now", label: "Yes, I have it now" }, { value: "earlier_resolved", label: "I had it earlier, but it has gone" }, { value: "unsure", label: "I am not sure" }] };
  it("explains why it is asked and submits the chosen option", async () => {
    ok();
    const done = vi.fn();
    render(<QuestionCard caseId="c1" q={q} onAnswered={done} />);
    await userEvent.click(screen.getByText("Why are we asking?"));
    expect(screen.getByText(q.reason)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "I had it earlier, but it has gone" }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(calls("/cases/c1/questions/q1/answer")[0].body).toEqual({ text: "", choice: "earlier_resolved" });
  });
  it("shows server validation errors", async () => {
    fail(422, "invalid_answer", "Please enter your age in years (1-120).");
    render(<QuestionCard caseId="c1" q={{ ...q, answer_type: "number", options: [], key: "age" }} onAnswered={() => {}} />);
    await userEvent.type(screen.getByLabelText("Your answer"), "abc");
    await userEvent.click(screen.getByRole("button", { name: "Answer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Please enter your age");
  });
});

describe("upload review card", () => {
  const base: UploadItem = { id: "u1", kind: "audio", filename: "voice.webm", mime: "audio/webm", size_bytes: 1, status: "needs_confirmation", error: null, method: "faster-whisper", detected_language: "hi", language_confidence: 0.83,
    flags: ["low_transcription_confidence", "language_uncertain"], mean_confidence: 0.55, confirmed: false, created_at: "", extracted_text: "mujhe bukhar hai", translated_text: "I have a fever", segments: [] };
  it("asks the patient to check, shows flags and the editable text, and confirms edits", async () => {
    ok();
    const changed = vi.fn();
    render(<UploadCard caseId="c1" up={base} onChanged={changed} editable />);
    expect(screen.getByText(/Low transcription confidence/)).toBeInTheDocument();
    expect(screen.getByText(/Spoken language: Hindi/)).toBeInTheDocument();
    expect(screen.getByText(/machine translation/)).toBeInTheDocument();
    const box = screen.getByLabelText(/What we heard/);
    await userEvent.clear(box);
    await userEvent.type(box, "mujhe bukhar nahi hai");
    await userEvent.click(screen.getByRole("button", { name: /confirm my corrected text/i }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls("/cases/c1/uploads/u1/confirm")[0].body).toEqual({ text: "mujhe bukhar nahi hai" });
  });
  it("explains a failed upload honestly and offers removal", () => {
    render(<UploadCard caseId="c1" up={{ ...base, status: "failed", error: "No readable text was found. Please upload a clearer photo." }} onChanged={() => {}} editable />);
    expect(screen.getByRole("alert")).toHaveTextContent("No readable text was found");
    expect(screen.getByRole("button", { name: "Remove this file" })).toBeInTheDocument();
  });
  it("shows progress while processing", () => {
    render(<UploadCard caseId="c1" up={{ ...base, status: "processing" }} onChanged={() => {}} editable />);
    expect(screen.getByText("Transcribing…")).toBeInTheDocument();
  });
});

describe("decision panel", () => {
  const detail = (over: any = {}): CaseDetail => ({
    case: { ...row(), confirmed_by: null, confirmed_at: null, created_at: "" } as any, messages: [], uploads: [], facts: [], conflicts: [], questions: [], decisions: [], notes: [], audit: [], conflict_outcomes: {}, assessment_history: [],
    assessment: { id: "a1", ruleset_version: "demo", demonstration_only: true, trigger: "submission", proposed_queue: "URGENT", summary: "s", triggered_rules: [], supporting_fact_ids: [], missing_information: [], open_conflict_ids: [], data_quality: [], failure: null, changed_from: null, created_at: "", sort_tier: 1, escalation: 0 },
    ...over,
  });
  it("classifies actions", () => {
    expect(actionFor("URGENT", "URGENT")).toBe("confirm");
    expect(actionFor("NORMAL", "URGENT")).toBe("escalate");
    expect(actionFor("URGENT", "NORMAL")).toBe("downgrade");
    expect(actionFor("NEEDS_REVIEW", "NORMAL")).toBe("override");
  });
  it("allows confirming the proposal without a reason", async () => {
    ok();
    const changed = vi.fn();
    render(<DecisionPanel d={detail()} caseId="c1" onChanged={changed} reviewers={[]} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm proposal" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls("/staff/cases/c1/decision")[0].body).toEqual({ assessment_id: "a1", final_queue: "URGENT", reason: "" });
  });
  it("requires a reason (10+ chars) to downgrade a proposal", async () => {
    ok();
    render(<DecisionPanel d={detail()} caseId="c1" onChanged={() => {}} reviewers={[]} />);
    await userEvent.click(screen.getByRole("radio", { name: /normal/i }));
    const btn = screen.getByRole("button", { name: "Record decision" });
    expect(btn).toBeDisabled();
    expect(screen.getByText(/downgrade/)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Reason/), "short");
    expect(btn).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Reason/), " but now long enough");
    expect(btn).toBeEnabled();
  });
  it("does not preselect a decision when the proposal is NEEDS_REVIEW", () => {
    render(<DecisionPanel d={detail({ assessment: { ...detail().assessment, proposed_queue: "NEEDS_REVIEW" } })} caseId="c1" onChanged={() => {}} reviewers={[]} />);
    expect(screen.getByRole("button", { name: "Record decision" })).toBeDisabled();
  });
  it("flags a proposal that changed since the human decision", () => {
    render(<DecisionPanel d={detail({ case: { ...detail().case, confirmed_queue: "NORMAL", confirmed_by: "Dr X", reassessment_pending: true } })} caseId="c1" onChanged={() => {}} reviewers={[]} />);
    expect(screen.getByText("Changed since your decision")).toBeInTheDocument();
  });
  it("surfaces a stale-assessment error from the server", async () => {
    fail(409, "stale_assessment", "The proposal changed since you opened this case.");
    render(<DecisionPanel d={detail()} caseId="c1" onChanged={() => {}} reviewers={[]} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm proposal" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("proposal changed");
  });
});
