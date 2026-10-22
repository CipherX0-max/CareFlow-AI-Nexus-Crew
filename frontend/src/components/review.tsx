"use client";
import { AlertTriangle, Check, CheckCircle2, FileText, Mic, ShieldAlert, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api, errMessage } from "@/lib/api";
import { flagText, human, langLabel, when } from "@/lib/format";
import type { Assessment, CaseDetail, Conflict, Fact, Message, Note, Question, Queue, UploadItem } from "@/lib/types";
import { Alert, Badge, Button, Dialog, Field, QueueBadge, Spinner, cx } from "./ui";

type Done = () => void | Promise<void>;
const POL_TONE = { present: "warn", denied: "success", uncertain: "neutral" } as const;
const POL_TXT = { present: "present", denied: "denied", uncertain: "uncertain" } as const;

function SourceTag({ f }: { f: { source: { type: string; locator: string | null } } }) {
  const Icon = f.source.type === "audio" ? Mic : f.source.type === "document" ? FileText : null;
  return <span className="inline-flex items-center gap-1 text-xs text-ink-soft">{Icon && <Icon className="h-3 w-3" aria-hidden />}{f.source.type}{f.source.locator ? ` · ${f.source.locator}` : ""}</span>;
}

// ------------------------------------------------------------------------------------------ overview
export function Overview({ d }: { d: CaseDetail }) {
  const a = d.assessment;
  const factById = useMemo(() => new Map(d.facts.map((f) => [f.id, f])), [d.facts]);
  if (!a) return <Alert tone="warn">No assessment yet - processing may still be running.</Alert>;
  return (
    <div className="space-y-5">
      <section className="card p-4">
        <div className="flex flex-wrap items-center gap-2"><h3 className="text-lg font-semibold">Why this proposal</h3><QueueBadge queue={a.proposed_queue} prefix="Proposed:" />
          {a.demonstration_only && <Badge tone="warn">Demonstration ruleset {a.ruleset_version}</Badge>}</div>
        <p className="mt-2 text-[15px]">{a.summary}</p>
        {a.changed_from && <p className="mt-1 text-sm text-amber-clinical">Changed from {a.changed_from} after: {human(a.trigger)}.</p>}
        {a.failure && <Alert tone="danger" className="mt-3" title="Automatic processing problem">{a.failure}. This case was routed to review rather than guessed.</Alert>}
      </section>
      {a.triggered_rules.length > 0 && (
        <section className="card p-4">
          <h3 className="text-lg font-semibold">Rules that matched <span className="text-sm font-normal text-ink-faint">(ruleset {a.ruleset_version})</span></h3>
          <ul className="mt-2 space-y-3">{a.triggered_rules.map((r) => (
            <li key={r.rule_id} className="rounded-lg border border-stone p-3">
              <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm font-semibold">{r.rule_id}</span><span className="font-medium">{r.name}</span><Badge>tier {r.tier}</Badge>{r.placeholder_threshold && <Badge tone="warn">placeholder threshold</Badge>}</div>
              <p className="mt-1 text-sm text-ink-soft">{r.rationale}</p>
              <ul className="mt-2 space-y-1">{r.supporting_fact_ids.map((id) => { const f = factById.get(id); return f ? (
                <li key={id} className="text-sm"><span className="font-medium">{f.label || f.concept}</span> <Badge tone={POL_TONE[f.polarity]}>{f.polarity}</Badge> <span className="text-ink-faint">“{f.excerpt}”</span> <SourceTag f={f} /></li>) : null; })}</ul>
            </li>))}</ul>
        </section>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <section className="card p-4"><h3 className="text-lg font-semibold">Missing information</h3>
          {a.missing_information.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{a.missing_information.map((m) => <li key={m.key}>{m.label}{m.blocking ? <span className="text-amber-clinical"> (blocks a Normal proposal)</span> : <span className="text-ink-faint"> (listed; does not lower urgency)</span>}</li>)}</ul>
            : <p className="mt-1 text-sm text-ink-soft">Nothing required is missing.</p>}</section>
        <section className="card p-4"><h3 className="text-lg font-semibold">Data quality and conflicts</h3>
          {a.data_quality.length || a.open_conflict_ids.length ? <ul className="mt-2 space-y-1 text-sm">{a.data_quality.map((q, i) => <li key={i} className="flex gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-clinical" aria-hidden />{q.message}</li>)}
            {a.open_conflict_ids.length > 0 && <li className="font-medium">{a.open_conflict_ids.length} open conflict(s) - see Conflicts tab.</li>}</ul>
            : <p className="mt-1 text-sm text-ink-soft">No open data-quality problems.</p>}</section>
      </div>
      <section className="card p-4"><h3 className="text-lg font-semibold">Assessment history</h3>
        <ol className="mt-2 space-y-1 text-sm">{d.assessment_history.map((h) => <li key={h.id} className="flex flex-wrap items-center gap-2"><span className="w-28 text-ink-faint">{when(h.created_at)}</span><QueueBadge queue={h.proposed_queue} /><span className="text-ink-soft">{human(h.trigger)}{h.changed_from ? ` · from ${h.changed_from}` : ""}</span></li>)}</ol></section>
    </div>
  );
}

// ------------------------------------------------------------------------------------------ evidence
export function Evidence({ d, caseId, onChanged }: { d: CaseDetail; caseId: string; onChanged: Done }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const ack = async (url: string, id: string) => { setBusy(id); setErr(""); try { await api(url, { json: {} }); await onChanged(); } catch (e) { setErr(errMessage(e)); } finally { setBusy(""); } };
  const pending = (flags: string[]) => flags.filter((f) => !f.startsWith("reviewed:"));
  return (
    <div className="space-y-5">
      {err && <Alert tone="danger">{err}</Alert>}
      <section><h3 className="mb-2 text-lg font-semibold">Patient messages (original wording preserved)</h3>
        {d.messages.filter((m) => m.kind !== "followup_answer").length === 0 && <p className="text-sm text-ink-soft">No typed messages.</p>}
        <ul className="space-y-3">{d.messages.map((m: Message) => (
          <li key={m.id} className="card p-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-faint"><span className="font-mono">#{m.seq}</span><span>{m.kind === "followup_answer" ? "answer to follow-up" : "typed message"}</span><span>{when(m.created_at)}</span>
              <Badge>{langLabel(m.language)}</Badge>{m.language_support === "limited" && <Badge tone="warn">limited automatic support - read the original</Badge>}</div>
            <p className="mt-1 whitespace-pre-wrap" lang={m.language === "en" ? "en" : "hi"}>{m.text}</p>
            {m.text_en && m.language !== "en" && <div className="mt-2 rounded bg-slate-soft p-2 text-sm"><div className="text-xs font-medium text-ink-faint">English rendering (machine translation)</div>{m.text_en}</div>}
            {pending(m.translation_flags).length > 0 && (
              <div className="mt-2 space-y-1">{pending(m.translation_flags).map((f) => <Alert key={f} tone="warn">Translation check: {flagText(f)}</Alert>)}
                <Button variant="secondary" loading={busy === m.id} onClick={() => ack(`/staff/cases/${caseId}/messages/${m.id}/acknowledge-flags`, m.id)}>I have checked the original wording</Button></div>)}
          </li>))}</ul></section>
      <section><h3 className="mb-2 text-lg font-semibold">Uploaded evidence</h3>
        {d.uploads.length === 0 && <p className="text-sm text-ink-soft">No files attached.</p>}
        <ul className="space-y-3">{d.uploads.map((u: UploadItem) => {
          const edited = u.confirmed_text != null && u.confirmed_text.trim() !== (u.extracted_text || "").trim();
          return (
            <li key={u.id} className="card p-3 space-y-2">
              <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{u.kind === "audio" ? "Voice recording" : u.kind === "pdf" ? "PDF" : "Photo"}: {u.filename}</span><Badge tone={u.status === "failed" ? "danger" : u.status === "confirmed" ? "success" : "warn"}>{u.status.replace("_", " ")}</Badge>
                <span className="text-xs text-ink-faint">{u.method}{u.detected_language ? ` · ${langLabel(u.detected_language)} ${u.language_confidence ? Math.round(u.language_confidence * 100) + "%" : ""}` : ""}{u.mean_confidence != null ? ` · confidence ${u.kind === "audio" ? Math.round(u.mean_confidence * 100) : Math.round(u.mean_confidence)}%` : ""}</span>
                <a className="btn-ghost ml-auto" href={`/api/cases/${caseId}/uploads/${u.id}/file`} target="_blank" rel="noreferrer">Open original</a></div>
              {u.error && <Alert tone="danger">{u.error}</Alert>}
              {u.flags.length > 0 && <div className="flex flex-wrap gap-1.5">{u.flags.map((f) => <Badge key={f} tone={f.startsWith("reviewed:") ? "neutral" : "warn"}>{flagText(f)}</Badge>)}</div>}
              {u.extracted_text && <details open={u.kind !== "pdf"}><summary className="cursor-pointer text-sm font-medium">Machine output {edited ? "(patient edited before confirming)" : ""}</summary><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-slate-soft p-2 text-xs">{u.extracted_text}</pre></details>}
              {edited && <details><summary className="cursor-pointer text-sm font-medium">Patient-confirmed text (used for extraction)</summary><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded bg-teal-soft p-2 text-xs">{u.confirmed_text}</pre></details>}
              {u.translated_text && <div className="rounded bg-slate-soft p-2 text-sm"><div className="text-xs font-medium text-ink-faint">English rendering (machine translation)</div>{u.translated_text}</div>}
              {pending(u.flags).some((f) => f.startsWith("asr_") || f.startsWith("negation") || f.startsWith("translation")) && u.status === "confirmed" &&
                <Button variant="secondary" loading={busy === u.id} onClick={() => ack(`/staff/cases/${caseId}/uploads/${u.id}/acknowledge-flags`, u.id)}>I have checked this transcript</Button>}
            </li>);
        })}</ul></section>
    </div>
  );
}

// ------------------------------------------------------------------------------------------ facts + vitals
const GROUPS: [string, (f: Fact) => boolean][] = [
  ["Symptoms", (f) => f.category === "symptom"], ["History and context", (f) => f.category === "history" || f.category === "context"],
  ["Medicines", (f) => f.category === "medication"], ["Allergies", (f) => f.category === "allergy"], ["Age", (f) => f.category === "demographic"], ["Report findings", (f) => f.category === "report_finding"],
];
export function Facts({ d, caseId, onChanged }: { d: CaseDetail; caseId: string; onChanged: Done }) {
  const [showSup, setShowSup] = useState(false);
  const [target, setTarget] = useState<{ f: Fact; v: "staff_verified" | "rejected" } | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [correctTarget, setCorrectTarget] = useState<Fact | null>(null);
  const [correction, setCorrection] = useState<Record<string, string>>({});
  const [correctionReason, setCorrectionReason] = useState("");
  const facts = d.facts.filter((f) => showSup || !f.superseded);
  const save = async () => {
    if (!target) return; setBusy(true); setErr("");
    try { await api(`/staff/cases/${caseId}/facts/${target.f.id}/verify`, { json: { verification: target.v, reason } }); setTarget(null); setReason(""); await onChanged(); } catch (e) { setErr(errMessage(e)); } finally { setBusy(false); }
  };
  const beginCorrection = (f: Fact) => {
    setCorrectTarget(f); setCorrection({ label: f.label, polarity: f.polarity, temporal: f.temporal, onset: f.onset || "", duration: f.duration || "",
      frequency: f.frequency || "", severity: f.severity || "", progression: f.progression || "", value_num: f.value == null ? "" : String(f.value),
      value_num2: f.value2 == null ? "" : String(f.value2), unit: f.unit || "" });
    setCorrectionReason(""); setErr("");
  };
  const saveCorrection = async () => {
    if (!correctTarget) return;
    setBusy(true); setErr("");
    try {
      const changes: Record<string, unknown> = { ...correction };
      for (const k of ["onset", "duration", "frequency", "severity", "progression", "unit"]) changes[k] = correction[k] || null;
      for (const k of ["value_num", "value_num2"]) changes[k] = correction[k] === "" ? null : Number(correction[k]);
      await api(`/staff/cases/${caseId}/facts/${correctTarget.id}/correct`, { method: "POST", json: { changes, reason: correctionReason } });
      setCorrectTarget(null); await onChanged();
    } catch (e) { setErr(errMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-ink-soft">“Extraction confidence” says how sure the software is that it read the text correctly. It is not a measure of clinical risk.</p>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showSup} onChange={(e) => setShowSup(e.target.checked)} />Show superseded statements</label>
      </div>
      {GROUPS.map(([title, test]) => {
        const rows = facts.filter(test);
        if (!rows.length) return null;
        return (
          <section key={title}><h3 className="mb-2 text-lg font-semibold">{title}</h3>
            <div className="overflow-x-auto rounded-xl border border-stone bg-paper"><table className="w-full min-w-[760px] text-sm"><thead className="bg-slate-soft text-left text-xs text-ink-soft"><tr>
              <th className="p-2">Finding</th><th className="p-2">Status</th><th className="p-2">Timing / detail</th><th className="p-2">Evidence</th><th className="p-2">Verification</th><th className="p-2" /></tr></thead>
              <tbody className="divide-y divide-stone">{rows.map((f) => (
                <tr key={f.id} className={cx(f.superseded && "opacity-50")}>
                  <td className="p-2 align-top"><div className="font-medium capitalize">{human(f.label || f.concept)}</div>{f.origin === "inferred" && <Badge tone="warn">AI inference</Badge>}{f.conflict_ids.length > 0 && <Badge tone="danger">in conflict</Badge>}</td>
                  <td className="p-2 align-top"><Badge tone={POL_TONE[f.polarity]}>{POL_TXT[f.polarity]}</Badge> <div className="text-xs text-ink-faint">{f.temporal}</div></td>
                  <td className="p-2 align-top text-xs">{[f.onset && `onset ${f.onset}`, f.duration && `duration ${f.duration}`, f.frequency && `frequency ${f.frequency}`, f.severity && `severity ${f.severity} (as reported)`, f.progression && f.progression.replace("_", " ")].filter(Boolean).join(" · ") || (f.value != null ? `${f.value}${f.value2 ? "/" + f.value2 : ""} ${f.unit ?? ""}` : "-")}</td>
                  <td className="p-2 align-top"><div className="text-ink-soft">“{f.excerpt.slice(0, 140)}”</div><SourceTag f={f} />
                    <div className="text-xs text-ink-faint">{f.method} · extraction confidence {f.confidence != null ? Math.round(f.confidence * 100) + "%" : "n/a"}</div>
                    {f.flags.filter((x) => !x.startsWith("superseded") && x !== "clarification").map((x) => <Badge key={x} tone="warn" className="mr-1 mt-1">{flagText(x)}</Badge>)}</td>
                  <td className="p-2 align-top"><Badge tone={f.verification === "staff_verified" || f.verification === "patient_confirmed" ? "success" : f.verification === "rejected" ? "danger" : "neutral"}>{f.verification.replace("_", " ")}</Badge></td>
                  <td className="p-2 align-top whitespace-nowrap">{!f.superseded && f.verification !== "rejected" && <><button className="mr-2 text-xs text-teal underline" onClick={() => beginCorrection(f)}>Correct</button><button className="mr-2 text-xs text-teal underline" onClick={() => setTarget({ f, v: "staff_verified" })}>Verify</button><button className="text-xs text-crimson-clinical underline" onClick={() => setTarget({ f, v: "rejected" })}>Reject</button></>}</td>
                </tr>))}</tbody></table></div></section>);
      })}
      {facts.length === 0 && <p className="text-sm text-ink-soft">No facts extracted.</p>}
      <Dialog open={!!target} onClose={() => setTarget(null)} title={target?.v === "rejected" ? "Reject this extracted fact" : "Verify this extracted fact"}
        footer={<><Button variant="secondary" onClick={() => setTarget(null)}>Cancel</Button><Button variant={target?.v === "rejected" ? "danger" : "primary"} onClick={save} loading={busy} disabled={reason.trim().length < 5}>Save</Button></>}>
        {err && <Alert tone="danger" className="mb-3">{err}</Alert>}
        <p className="mb-3 text-sm">“{target?.f.excerpt}” → <b>{target && human(target.f.label || target.f.concept)}</b> ({target?.f.polarity}). The original statement is kept; the action is audited and the case is reassessed.</p>
        <Field label="Reason (required)" htmlFor="vr"><textarea id="vr" className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </Dialog>
      <Dialog open={!!correctTarget} onClose={() => setCorrectTarget(null)} title="Correct extracted details" wide
        footer={<><Button variant="secondary" onClick={() => setCorrectTarget(null)}>Cancel</Button><Button onClick={saveCorrection} loading={busy} disabled={correctionReason.trim().length < 10}>Save correction</Button></>}>
        {err && <Alert tone="danger" className="mb-3">{err}</Alert>}
        <Alert tone="info" className="mb-3">The original source fact stays in the record as superseded. This correction is added as a separate staff-verified fact and the case is reassessed.</Alert>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Finding label" htmlFor="fc-label"><input id="fc-label" className="input" value={correction.label || ""} onChange={(e) => setCorrection({ ...correction, label: e.target.value })} /></Field>
          <Field label="Reported status" htmlFor="fc-pol"><select id="fc-pol" className="input" value={correction.polarity || "uncertain"} onChange={(e) => setCorrection({ ...correction, polarity: e.target.value })}><option value="present">Present</option><option value="denied">Denied</option><option value="uncertain">Uncertain</option></select></Field>
          <Field label="Timing" htmlFor="fc-temp"><select id="fc-temp" className="input" value={correction.temporal || "unknown"} onChange={(e) => setCorrection({ ...correction, temporal: e.target.value })}><option value="current">Current</option><option value="past">Past</option><option value="resolved">Resolved</option><option value="unknown">Unknown</option></select></Field>
          {["onset", "duration", "frequency", "severity", "progression", ...(correctTarget?.value != null ? ["value_num", "value_num2", "unit"] : [])].map((k) => <Field key={k} label={k === "value_num" ? "Value" : k === "value_num2" ? "Second value" : human(k)} htmlFor={`fc-${k}`}><input id={`fc-${k}`} className="input" type={k.startsWith("value_num") ? "number" : "text"} value={correction[k] || ""} onChange={(e) => setCorrection({ ...correction, [k]: e.target.value })} /></Field>)}
          <div className="sm:col-span-2"><Field label="Reason for correction (required)" htmlFor="fc-reason"><textarea id="fc-reason" className="input min-h-[76px]" value={correctionReason} onChange={(e) => setCorrectionReason(e.target.value)} /></Field></div>
        </div>
      </Dialog>
    </div>
  );
}

export function Vitals({ d }: { d: CaseDetail }) {
  const v = d.facts.filter((f) => f.category === "vital" && !f.superseded);
  if (!v.length) return <Alert tone="info">No vital signs were provided. Missing measurements are listed, never assumed normal.</Alert>;
  return (
    <div className="overflow-x-auto rounded-xl border border-stone bg-paper"><table className="w-full min-w-[640px] text-sm"><thead className="bg-slate-soft text-left text-xs text-ink-soft"><tr><th className="p-2">Measurement</th><th className="p-2">Value</th><th className="p-2">Measured</th><th className="p-2">Source</th><th className="p-2">Checks</th></tr></thead>
      <tbody className="divide-y divide-stone">{v.map((f) => (
        <tr key={f.id}><td className="p-2 font-medium capitalize">{human(f.concept)}</td>
          <td className="p-2">{f.concept === "blood_pressure" ? `${f.value}/${f.value2}` : f.value} <span className="text-ink-faint">{f.unit || "(no unit)"}</span></td>
          <td className="p-2">{f.measured_at ? when(f.measured_at) : <span className="text-amber-clinical">time not stated</span>}</td>
          <td className="p-2"><SourceTag f={f} /><div className="text-xs text-ink-faint">“{f.excerpt.slice(0, 60)}”</div></td>
          <td className="p-2">{f.flags.filter((x) => !x.startsWith("superseded") && x !== "clarification").length ? f.flags.filter((x) => !x.startsWith("superseded") && x !== "clarification").map((x) => <Badge key={x} tone="warn" className="mr-1">{flagText(x)}</Badge>) : <Badge tone="success">no problems found</Badge>}</td></tr>))}</tbody></table></div>
  );
}

// ------------------------------------------------------------------------------------------ conflicts
const OUTCOME_TXT: Record<string, string> = { present_now: "Present now", earlier_resolved: "Was present earlier, now resolved", not_present: "Not present", use_a: "First statement is correct", use_b: "Second statement is correct", unit_F: "Unit is °F", unit_C: "Unit is °C" };
export function Conflicts({ d, caseId, onChanged }: { d: CaseDetail; caseId: string; onChanged: Done }) {
  const [target, setTarget] = useState<Conflict | null>(null);
  const [outcome, setOutcome] = useState("");
  const [note, setNote] = useState("");
  const [ev, setEv] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const open = (c: Conflict) => { setTarget(c); setOutcome(""); setNote(""); setEv(c.evidence.map((e) => e.fact_id!).filter(Boolean)); setErr(""); };
  const save = async () => {
    if (!target) return; setBusy(true); setErr("");
    try { await api(`/staff/cases/${caseId}/conflicts/${target.id}/resolve`, { json: { outcome, note, evidence_considered: ev } }); setTarget(null); await onChanged(); } catch (e) { setErr(errMessage(e)); } finally { setBusy(false); }
  };
  if (!d.conflicts.length) return <Alert tone="success">No conflicts detected between statements, sources or measurements.</Alert>;
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-soft">Conflicts are never auto-resolved. Resolving one records who decided, when, and what evidence was considered; the original conflict stays on file.</p>
      {d.conflicts.map((c) => (
        <section key={c.id} className={cx("card p-4", c.status === "unresolved" && (c.safety_critical ? "border-crimson-clinical" : "border-amber-clinical"))}>
          <div className="flex flex-wrap items-center gap-2"><h3 className="text-lg font-semibold capitalize">{human(c.concept)}</h3><Badge>{c.kind.replace("_", " ")}</Badge>
            {c.safety_critical && <Badge tone="danger"><ShieldAlert className="h-3 w-3" />safety-critical</Badge>}
            <Badge tone={c.status === "unresolved" ? "warn" : "success"}>{c.status.replace("_", " ")}</Badge></div>
          <p className="mt-2 text-sm">{c.description}</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">{c.evidence.filter((e) => e.fact_id).map((e) => (
            <blockquote key={e.fact_id} className="rounded-lg border border-stone bg-slate-soft p-2 text-sm"><div className="mb-1 flex flex-wrap gap-1.5 text-xs"><Badge tone={e.polarity === "denied" ? "success" : "warn"}>{e.polarity}</Badge><span className="text-ink-faint">{e.source_type} · {e.locator || ""}</span></div>“{e.excerpt}”</blockquote>))}</div>
          {c.llm_opinion && <p className="mt-2 text-xs text-ink-soft"><b>Advisory AI note (not a resolution):</b> {human(c.llm_opinion.classification)} - {c.llm_opinion.rationale}</p>}
          {c.status !== "unresolved" && <div className="mt-3 rounded-lg bg-teal-soft p-3 text-sm"><b>{c.status === "clarified" ? "Patient clarified" : "Reviewer resolved"}:</b> {OUTCOME_TXT[c.clarification || ""] || c.clarification}{c.resolution_text ? ` - “${c.resolution_text}”` : ""}
            <div className="text-xs text-ink-soft">{c.resolved_by ? `by ${c.resolved_by}` : "by patient"} · {when(c.resolved_at)}{c.status === "clarified" && c.safety_critical ? " · a reviewer must still confirm safety-critical clarifications" : ""}</div></div>}
          {c.status !== "reviewer_resolved" && <Button className="mt-3" variant={c.status === "unresolved" ? "primary" : "secondary"} onClick={() => open(c)}><Check className="h-4 w-4" />{c.status === "clarified" ? "Review and resolve" : "Resolve as reviewer"}</Button>}
        </section>))}
      <Dialog open={!!target} onClose={() => setTarget(null)} title="Resolve conflict" wide
        footer={<><Button variant="secondary" onClick={() => setTarget(null)}>Cancel</Button><Button onClick={save} loading={busy} disabled={!outcome || note.trim().length < 10}>Record resolution</Button></>}>
        {err && <Alert tone="danger" className="mb-3">{err}</Alert>}
        {target && <>
          <p className="mb-3 text-sm">{target.description}</p>
          <fieldset className="mb-3"><legend className="label">What is the correct position?</legend>
            {(d.conflict_outcomes[target.id] || []).map((o) => <label key={o} className="mb-1 flex items-center gap-2 text-sm"><input type="radio" name="o" checked={outcome === o} onChange={() => setOutcome(o)} />{OUTCOME_TXT[o] || o}</label>)}</fieldset>
          <fieldset className="mb-3"><legend className="label">Evidence considered</legend>
            {target.evidence.filter((e) => e.fact_id).map((e) => <label key={e.fact_id} className="mb-1 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ev.includes(e.fact_id!)} onChange={(x) => setEv(x.target.checked ? [...ev, e.fact_id!] : ev.filter((i) => i !== e.fact_id))} /><span>“{e.excerpt}” <span className="text-ink-faint">({e.source_type}, {e.locator})</span></span></label>)}</fieldset>
          <Field label="How did you decide? (required, 10+ characters)" htmlFor="rn"><textarea id="rn" className="input min-h-[80px]" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </>}
      </Dialog>
    </div>
  );
}

export function Questions({ qs }: { qs: Question[] }) {
  if (!qs.length) return <p className="text-sm text-ink-soft">No follow-up questions have been asked.</p>;
  return (
    <ul className="space-y-3">{qs.filter((q) => q.status !== "deferred").map((q) => (
      <li key={q.id} className="card p-3"><div className="flex flex-wrap items-center gap-2"><Badge tone={q.status === "open" ? "warn" : q.status === "answered" ? "success" : "neutral"}>{q.status}</Badge><span className="text-xs text-ink-faint">asked {when(q.asked_at)}</span></div>
        <p className="mt-1 font-medium">{q.text}</p><p className="text-xs text-ink-soft">Reason: {q.reason}</p>
        {q.status !== "open" && <p className="mt-1 text-sm">{q.status === "answered" ? <>Answer: <b>{q.answer_choice ? (q.options.find((o) => o.value === q.answer_choice)?.label ?? q.answer_choice) : q.answer_text}</b>{q.answer_choice && q.answer_text ? ` - ${q.answer_text}` : ""}</> : q.status === "skipped" ? "Patient was unsure / could not answer (recorded as unknown, not as “no”)." : "Withdrawn - no longer needed."}</p>}
      </li>))}</ul>
  );
}

// ------------------------------------------------------------------------------------------ decision panel
export function actionFor(proposed: Queue, final: Queue) {
  return final === proposed ? "confirm" : final === "URGENT" ? "escalate" : proposed === "URGENT" ? "downgrade" : "override";
}
export function DecisionPanel({ d, caseId, onChanged, reviewers }: { d: CaseDetail; caseId: string; onChanged: Done; reviewers: { id: string; name: string }[] }) {
  const a = d.assessment;
  const [final, setFinal] = useState<Queue | "">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ask, setAsk] = useState(false);
  const [q, setQ] = useState({ text: "", reason: "" });
  if (!a) return null;
  const chosen = final || (a.proposed_queue === "NEEDS_REVIEW" ? "" : a.proposed_queue);
  const action = chosen ? actionFor(a.proposed_queue, chosen as Queue) : null;
  const needReason = action && action !== "confirm";
  const run = async (fn: () => Promise<unknown>) => { setBusy(true); setErr(""); try { await fn(); await onChanged(); } catch (e) { setErr(errMessage(e)); } finally { setBusy(false); } };
  const closed = d.case.status === "resolved";
  return (
    <aside className="card sticky top-4 space-y-4 p-4" aria-label="Decision">
      <div><div className="text-xs font-medium uppercase tracking-wide text-ink-faint">Proposal (rules engine)</div><div className="mt-1 flex items-center gap-2"><QueueBadge queue={a.proposed_queue} />{a.demonstration_only && <Badge tone="warn">demo rules</Badge>}</div></div>
      <div><div className="text-xs font-medium uppercase tracking-wide text-ink-faint">Confirmed by staff</div>
        {d.case.confirmed_queue ? <div className="mt-1 flex flex-wrap items-center gap-2"><QueueBadge queue={d.case.confirmed_queue} /><span className="text-xs text-ink-soft">by {d.case.confirmed_by} · {when(d.case.confirmed_at)}</span></div> : <p className="mt-1 text-sm text-ink-soft">No human decision yet.</p>}
        {d.case.reassessment_pending && <Alert tone="warn" className="mt-2" title="Changed since your decision">New information changed the proposal. Review and re-confirm.</Alert>}</div>
      {err && <Alert tone="danger">{err}</Alert>}
      {!closed && (
        <form className="space-y-3 border-t border-stone pt-3" onSubmit={(e) => { e.preventDefault(); if (chosen) run(() => api(`/staff/cases/${caseId}/decision`, { json: { assessment_id: a.id, final_queue: chosen, reason } }).then(() => { setReason(""); setFinal(""); })); }}>
          <fieldset><legend className="label">Your decision</legend>
            {(["URGENT", "NEEDS_REVIEW", "NORMAL"] as Queue[]).map((qn) => (
              <label key={qn} className={cx("mb-1.5 flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm", chosen === qn ? "border-teal bg-teal-soft" : "border-stone")}>
                <input type="radio" name="final" checked={chosen === qn} onChange={() => setFinal(qn)} /><QueueBadge queue={qn} />{qn === a.proposed_queue && <span className="text-xs text-ink-faint">(proposed)</span>}</label>))}
          </fieldset>
          {action && <p className="text-xs text-ink-soft">This will be recorded as: <b>{action}</b>{action === "confirm" ? "" : " - reason required"}.</p>}
          {a.proposed_queue === "NEEDS_REVIEW" && !final && <p className="text-xs text-ink-soft">The proposal is “needs review”: resolve the open items or choose a category with a reason.</p>}
          <Field label={needReason ? "Reason (required, 10+ characters)" : "Note (optional)"} htmlFor="dr"><textarea id="dr" className="input min-h-[80px]" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          <Button type="submit" className="w-full" loading={busy} disabled={!chosen || (!!needReason && reason.trim().length < 10)}><CheckCircle2 className="h-4 w-4" />{action === "confirm" ? "Confirm proposal" : "Record decision"}</Button>
        </form>)}
      <div className="space-y-2 border-t border-stone pt-3">
        <label className="label" htmlFor="as">Assigned reviewer</label>
        <select id="as" className="input" value={d.case.assigned_to || ""} onChange={(e) => run(() => api(`/staff/cases/${caseId}/assign`, { json: { user_id: e.target.value || null } }))}><option value="">Unassigned</option>{reviewers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setAsk(true)}>Ask patient</Button>
          <Button variant="secondary" onClick={() => run(() => api(`/staff/cases/${caseId}/reassess`, { json: {} }))}>Re-run rules</Button>
          {!closed ? <Button variant="secondary" onClick={() => run(() => api(`/staff/cases/${caseId}/status`, { json: { status: "resolved" } }))} disabled={!d.case.confirmed_queue || d.case.reassessment_pending}><Check className="h-4 w-4" />Resolve</Button>
            : <Button variant="secondary" onClick={() => run(() => api(`/staff/cases/${caseId}/status`, { json: { status: "in_review" } }))}><Undo2 className="h-4 w-4" />Reopen</Button>}
        </div>
      </div>
      <Dialog open={ask} onClose={() => setAsk(false)} title="Ask the patient a question" footer={<><Button variant="secondary" onClick={() => setAsk(false)}>Cancel</Button><Button disabled={q.text.trim().length < 5 || q.reason.trim().length < 5} loading={busy}
        onClick={() => run(() => api(`/staff/cases/${caseId}/questions`, { json: q }).then(() => { setAsk(false); setQ({ text: "", reason: "" }); }))}>Send question</Button></>}>
        <div className="space-y-3"><Field label="Question" htmlFor="qt"><textarea id="qt" className="input" value={q.text} onChange={(e) => setQ({ ...q, text: e.target.value })} /></Field><Field label="Why you are asking (shown to the patient)" htmlFor="qr"><input id="qr" className="input" value={q.reason} onChange={(e) => setQ({ ...q, reason: e.target.value })} /></Field></div>
      </Dialog>
    </aside>
  );
}

// ------------------------------------------------------------------------------------------ note
const TEXT_SECTIONS: [string, string][] = [["presenting_complaint", "Presenting complaint"], ["proposed_urgency", "Proposed urgency and explanation"], ["reviewer_comments", "Reviewer comments"], ["final_disposition", "Final disposition"]];
const LIST_SECTIONS: [string, string][] = [["symptom_timeline", "Symptoms and timeline"], ["relevant_history", "History, medicines, allergies"], ["vital_signs", "Vital signs (with units and times)"], ["report_findings", "Report findings and sources"]];
export function NoteTab({ d, caseId, onChanged }: { d: CaseDetail; caseId: string; onChanged: Done }) {
  const note: Note | undefined = d.notes[0];
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [reason, setReason] = useState("");
  const [dlg, setDlg] = useState<"approve" | "reject" | null>(null);
  const [text, setText] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>) => { setBusy(key); setErr(""); try { await fn(); await onChanged(); } catch (e) { setErr(errMessage(e)); } finally { setBusy(""); } };
  if (!note) return (
    <div className="space-y-3">{err && <Alert tone="danger">{err}</Alert>}
      <Alert tone="info">No note yet. Generate a structured DRAFT from the current evidence; it stays a draft until a clinician edits and approves it.</Alert>
      <Button loading={busy === "gen"} onClick={() => run("gen", () => api(`/staff/cases/${caseId}/notes/draft`, { json: {} }))}>Generate draft note</Button></div>);
  const c = note.content;
  const editable = note.status === "draft";
  const val = (k: string, isList: boolean) => edits[k] ?? (isList ? ((c[k] as string[]) || []).join("\n") : String(c[k] ?? ""));
  const dirty = Object.keys(edits).length > 0;
  const save = () => run("save", async () => {
    const changes: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(edits)) changes[k] = LIST_SECTIONS.some(([lk]) => lk === k) ? v.split("\n").map((x) => x.trim()).filter(Boolean) : v;
    await api(`/staff/cases/${caseId}/notes/${note.id}`, { method: "PUT", json: { changes } }); setEdits({});
  });
  const f = c.findings || {};
  return (
    <div className="space-y-4">
      {err && <Alert tone="danger">{err}</Alert>}
      <div className={cx("rounded-lg border-2 p-3", note.status === "approved" ? "border-teal bg-teal-soft" : note.status === "draft" ? "border-amber-line bg-amber-soft" : "border-stone bg-slate-soft")}>
        <div className="flex flex-wrap items-center gap-2"><b>Version {note.version}: {note.label}</b>{note.stale && <Badge tone="danger">outdated - case changed since generated</Badge>}</div>
        <p className="mt-1 text-xs">{c.disclaimer}</p>
        {note.status === "approved" && <p className="mt-1 text-sm">Approved {when(note.reviewed_at)}{note.review_reason ? ` - “${note.review_reason}”` : ""}.</p>}
        {note.status === "rejected" && <p className="mt-1 text-sm">Rejected: {note.review_reason}</p>}
      </div>
      {TEXT_SECTIONS.map(([k, l]) => <Field key={k} label={l} htmlFor={`n-${k}`}><textarea id={`n-${k}`} className="input min-h-[72px]" readOnly={!editable} value={val(k, false)} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} /></Field>)}
      {LIST_SECTIONS.map(([k, l]) => <Field key={k} label={l} htmlFor={`n-${k}`} hint="One item per line."><textarea id={`n-${k}`} className="input min-h-[72px]" readOnly={!editable} value={val(k, true)} onChange={(e) => setEdits({ ...edits, [k]: e.target.value })} /></Field>)}
      <section className="card p-3"><h4 className="font-semibold">Findings (generated; see Facts and Conflicts tabs to change)</h4>
        {[["positive", "Positive"], ["negative_explicitly_denied", "Explicitly denied"], ["uncertain", "Uncertain"], ["conflicting", "Conflicting"], ["not_provided", "Not provided"], ["resolved_or_past", "Resolved / past"]].map(([k, l]) => (f[k]?.length ? <div key={k} className="mt-2"><div className="text-xs font-medium text-ink-faint">{l}</div><ul className="list-disc pl-5 text-sm">{f[k].map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div> : null))}
        {c.outstanding_and_uncertain && [["open_questions", "Open questions"], ["data_quality", "Data quality"], ["unresolved_conflicts", "Unresolved conflicts"]].map(([k, l]) => (c.outstanding_and_uncertain[k]?.length ? <div key={k} className="mt-2"><div className="text-xs font-medium text-ink-faint">{l}</div><ul className="list-disc pl-5 text-sm">{c.outstanding_and_uncertain[k].map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div> : null))}</section>
      {note.change_summary.length > 0 && <details><summary className="cursor-pointer text-sm font-medium">Reviewer edits ({note.change_summary.length})</summary><ul className="mt-1 space-y-1 text-xs">{note.change_summary.map((ch: any, i: number) => <li key={i}><b>{ch.section}</b> edited {when(ch.at)}</li>)}</ul></details>}
      <div className="flex flex-wrap gap-2">
        {editable && <Button variant="secondary" onClick={save} disabled={!dirty} loading={busy === "save"}>Save edits</Button>}
        {editable && <Button onClick={() => { setReason(""); setDlg("approve"); }} disabled={dirty || note.stale}><CheckCircle2 className="h-4 w-4" />Approve as clinician</Button>}
        {editable && <Button variant="danger" onClick={() => { setReason(""); setDlg("reject"); }}>Reject draft</Button>}
        {(note.status !== "draft" || note.stale) && <Button variant="secondary" loading={busy === "gen"} onClick={() => run("gen", () => api(`/staff/cases/${caseId}/notes/draft`, { json: {} }))}>Generate new draft from latest evidence</Button>}
        <Button variant="ghost" onClick={async () => setText((await api(`/staff/cases/${caseId}/notes/${note.id}/text`)).text)}>View as text</Button>
      </div>
      {!d.case.confirmed_queue && editable && <p className="text-xs text-amber-clinical">Record a human urgency decision before approving the note.</p>}
      <Dialog open={!!dlg} onClose={() => setDlg(null)} title={dlg === "approve" ? "Approve this note" : "Reject this draft"}
        footer={<><Button variant="secondary" onClick={() => setDlg(null)}>Cancel</Button><Button variant={dlg === "reject" ? "danger" : "primary"} loading={busy === "rev"} disabled={dlg === "reject" && reason.trim().length < 10}
          onClick={() => run("rev", () => api(`/staff/cases/${caseId}/notes/${note.id}/${dlg}`, { json: { reason } }).then(() => setDlg(null)))}>{dlg === "approve" ? "Approve" : "Reject"}</Button></>}>
        {dlg === "approve" ? <p className="mb-3 text-sm">By approving you confirm, as the responsible clinician, that you have reviewed the evidence and this note. Your identity and the time are recorded. The approved version becomes read-only.</p> : <p className="mb-3 text-sm">The rejected draft stays on record. Say what is wrong.</p>}
        <Field label={dlg === "reject" ? "Reason (required)" : "Comment (optional)"} htmlFor="nr"><textarea id="nr" className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      </Dialog>
      <Dialog open={text !== null} onClose={() => setText(null)} title="Note as plain text" wide footer={<Button variant="secondary" onClick={() => navigator.clipboard?.writeText(text || "")}>Copy</Button>}><pre className="whitespace-pre-wrap text-xs">{text}</pre></Dialog>
    </div>
  );
}

export function AuditTab({ d }: { d: CaseDetail }) {
  return (
    <ol className="space-y-1.5 text-sm">{d.audit.map((e) => (
      <li key={e.id} className="flex flex-wrap gap-x-3 border-b border-stone/60 pb-1.5"><span className="w-32 shrink-0 text-ink-faint">{when(e.ts)}</span><span className="font-mono text-xs">{e.action}</span><span className="text-ink-soft">{e.actor}</span>
        <span className="min-w-0 break-words text-xs text-ink-faint">{Object.entries(e.details).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(",") : String(v)}`).join(" · ")}</span></li>))}</ol>
  );
}
