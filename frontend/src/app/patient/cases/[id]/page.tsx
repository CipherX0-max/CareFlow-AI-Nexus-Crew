"use client";
import { ChevronLeft, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useState } from "react";
import { Shell } from "@/components/Shell";
import { Attachments, Composer, EmergencyBanner, QuestionCard, StatusTracker, UploadCard, Understood } from "@/components/patient";
import { Alert, Badge, Button, Dialog, Skeleton, Spinner, useAsync, useToast } from "@/components/ui";
import { api, ApiError, errMessage } from "@/lib/api";
import { RequireRole, useAuth } from "@/lib/auth";
import { langLabel, when } from "@/lib/format";
import type { EmergencyResult, PatientCase, PublicConfig } from "@/lib/types";

function CaseView({ id }: { id: string }) {
  const { me } = useAuth();
  const toast = useToast();
  const config = useAsync(() => api<PublicConfig>("/config/public"), []);
  const [emergency, setEmergency] = useState<EmergencyResult | null>(null);
  const [serverBusy, setServerBusy] = useState(false);
  const [until, setUntil] = useState(0); // poll quickly for a while after the patient acts (AI processing runs in the background)
  const fast = serverBusy || Date.now() < until;
  const c = useAsync(async () => {
    const v = await api<PatientCase>(`/cases/${id}`);
    setServerBusy(v.processing || v.uploads.some((u) => u.status === "pending" || u.status === "processing"));
    return v;
  }, [id], { poll: fast ? 2000 : 12000 });
  const [confirmSubmit, setConfirmSubmit] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const onEmergency = useCallback((e: EmergencyResult) => setEmergency(e), []);
  const reload = () => { setUntil(Date.now() + 45000); return c.reloadSilent(); };

  if (c.loading && !c.data) return <div className="space-y-4"><Skeleton className="h-10 w-64" /><Skeleton className="h-48" /><Skeleton className="h-32" /></div>;
  if (c.error && !c.data) {
    const nf = c.error instanceof ApiError && c.error.status === 404;
    return <Alert tone="danger" title={nf ? "Case not found" : "Could not load this case"} action={!nf && <Button variant="secondary" onClick={c.reload}>Retry</Button>}>{nf ? "It may have been deleted, or it is not yours." : errMessage(c.error)}</Alert>;
  }
  const v = c.data!;
  const lang = v.language === "hi" || v.language === "hi-Latn" ? v.language : me?.profile?.preferred_language || "en";
  const closed = v.status === "deletion_requested";
  const draft = v.status === "draft";
  const openQs = v.questions.filter((q) => q.status === "open");
  const anyEmergency = emergency?.triggered || v.emergency_flagged;
  const emergencyView: EmergencyResult | null = emergency?.triggered ? emergency : v.emergency_flagged && config.data ? {
    triggered: true, concepts: [], language: v.language, message: { en: config.data.emergency.message_en, hi: config.data.emergency.message_hi, "hi-Latn": config.data.emergency.message_hi_Latn }, note: config.data.emergency.clinician_reviewed ? "" : "This warning text has not been reviewed by a clinician (demonstration).",
  } : null;
  const hasUnconfirmed = v.uploads.some((u) => u.status === "needs_confirmation");

  const submit = async () => {
    setBusy(true);
    try { await api(`/cases/${id}/submit`, { json: {} }); setConfirmSubmit(false); toast("Submitted to the care team."); await c.reload(); }
    catch (e) { toast(errMessage(e), "danger"); } finally { setBusy(false); }
  };
  const requestDelete = async () => {
    setBusy(true);
    try { await api(`/cases/${id}/deletion-request`, { json: { reason } }); setConfirmDelete(false); toast("Deletion requested."); await c.reload(); }
    catch (e) { toast(errMessage(e), "danger"); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-6">
      <div>
        <Link href="/patient" className="inline-flex items-center gap-1 text-sm text-ink-soft hover:text-ink"><ChevronLeft className="h-4 w-4" />All cases</Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-3xl font-semibold">{draft ? "Your intake" : "Your case"}</h1>
          <span className="font-mono text-sm text-ink-soft">{v.case_number}</span>
          <Badge tone={draft ? "neutral" : "info"}>{v.status_label}</Badge>
          {c.stale && c.data && <Badge tone="warn" title={errMessage(c.error)}>Reconnecting…</Badge>}
          {v.processing && <Badge tone="info"><Spinner className="h-3 w-3" />Reading your information…</Badge>}
        </div>
      </div>

      {anyEmergency && <EmergencyBanner result={emergencyView} lang={lang} config={config.data} />}
      {closed && <Alert tone="info" title="Deletion requested">An administrator will review your request. Records that must be retained (for example an approved clinical note) may be kept.</Alert>}
      {!draft && !closed && <div className="card flex flex-wrap items-center justify-between gap-3 p-4"><StatusTracker status={v.status} /><p className="text-sm text-ink-soft">Submitted {when(v.submitted_at)}. You can still add information below.</p></div>}

      {openQs.length > 0 && !closed && (
        <section aria-labelledby="q-h" className="space-y-3">
          <h2 id="q-h" className="text-xl font-semibold">{openQs.length === 1 ? "A question for you" : "Questions for you"}</h2>
          <p className="text-sm text-ink-soft">Short answers help. Choosing "not sure" or skipping is fine - we never treat that as "no".</p>
          {openQs.map((q) => <QuestionCard key={q.id} caseId={id} q={q} onAnswered={reload} />)}
        </section>
      )}

      <section aria-labelledby="m-h" className="card p-5 space-y-4">
        <h2 id="m-h" className="text-xl font-semibold">What you have told us</h2>
        {v.messages.filter((m) => m.kind === "text").length === 0 && <p className="text-sm text-ink-soft">Nothing yet. Describe how you feel below, or record your voice.</p>}
        <ul className="space-y-2">
          {v.messages.filter((m) => m.kind === "text").map((m) => (
            <li key={m.id} className="rounded-lg bg-slate-soft p-3">
              <p className="whitespace-pre-wrap">{m.text}</p>
              <p className="mt-1 text-xs text-ink-faint">{when(m.created_at)} · {langLabel(m.language)}{m.language_support === "limited" && " · limited automatic support, staff will read this carefully"}</p>
            </li>
          ))}
        </ul>
        {!closed && <Composer caseId={id} maxChars={config.data?.limits.max_message_chars ?? 5000} onSent={reload} onEmergency={onEmergency} />}
      </section>

      {!closed && (
        <section aria-labelledby="a-h" className="card p-5 space-y-4">
          <h2 id="a-h" className="text-xl font-semibold">Voice, reports and photos <span className="text-sm font-normal text-ink-faint">(optional)</span></h2>
          <Attachments caseId={id} config={config.data} onUploaded={reload} />
          {v.uploads.length > 0 && <div className="space-y-3">{v.uploads.map((u) => <UploadCard key={u.id} caseId={id} up={u} onChanged={reload} editable={!closed} />)}</div>}
        </section>
      )}

      <section aria-labelledby="u-h" className="card p-5">
        <h2 id="u-h" className="text-xl font-semibold">What we understood so far</h2>
        <p className="mb-3 text-sm text-ink-soft">Please check this list. If something is wrong, tell us in a new message or answer the questions above.</p>
        <Understood items={v.understood} />
      </section>

      {draft && (
        <section className="card flex flex-col gap-3 border-teal/40 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div><h2 className="text-xl font-semibold">Ready to send to the care team?</h2><p className="text-sm text-ink-soft">They only see your case after you submit. You can add more later.</p>
            {hasUnconfirmed && <p className="mt-1 text-sm text-amber-clinical">You have uploads waiting for your check. Unchecked files are not used.</p>}</div>
          <Button onClick={() => setConfirmSubmit(true)} disabled={!v.messages.length && !v.uploads.some((u) => u.status === "confirmed")}><Send className="h-4 w-4" />Submit to care team</Button>
        </section>
      )}

      {!closed && <div className="border-t border-stone pt-4"><button className="inline-flex items-center gap-1 text-sm text-ink-soft underline" onClick={() => setConfirmDelete(true)}><Trash2 className="h-3.5 w-3.5" />Request deletion of this case</button></div>}

      <Dialog open={confirmSubmit} onClose={() => setConfirmSubmit(false)} title="Submit to the care team?"
        footer={<><Button variant="secondary" onClick={() => setConfirmSubmit(false)}>Not yet</Button><Button onClick={submit} loading={busy}>Submit</Button></>}>
        <p className="text-sm">A member of the care team will review your case. This is not real-time monitoring: if you feel worse or unsafe, call your local emergency number.</p>
      </Dialog>
      <Dialog open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Request deletion"
        footer={<><Button variant="secondary" onClick={() => setConfirmDelete(false)}>Cancel</Button><Button variant="danger" onClick={requestDelete} loading={busy}>Request deletion</Button></>}>
        <p className="mb-3 text-sm">Your case leaves the care team's queues now and an administrator will delete its contents. Some records may need to be kept for a legally required period.</p>
        <label className="label" htmlFor="why">Reason (optional)</label>
        <textarea id="why" className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
      </Dialog>
    </div>
  );
}

export default function Page() {
  const { id } = useParams<{ id: string }>();
  return <RequireRole role="patient"><Shell><CaseView id={id} /></Shell></RequireRole>;
}
