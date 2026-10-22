"use client";
import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Shell } from "@/components/Shell";
import { AuditTab, Conflicts, DecisionPanel, Evidence, Facts, NoteTab, Overview, Questions, Vitals } from "@/components/review";
import { Alert, Badge, Button, QueueBadge, Skeleton, Spinner, Tabs, useAsync } from "@/components/ui";
import { api, ApiError, errMessage } from "@/lib/api";
import { RequireRole } from "@/lib/auth";
import { human, langLabel, waiting } from "@/lib/format";
import type { CaseDetail } from "@/lib/types";

type T = "overview" | "evidence" | "facts" | "vitals" | "conflicts" | "questions" | "note" | "audit";

function Review({ id }: { id: string }) {
  const [tab, setTab] = useState<T>("overview");
  const d = useAsync(() => api<CaseDetail>(`/staff/cases/${id}`), [id], { poll: 8000 });
  const rev = useAsync(() => api<{ reviewers: { id: string; name: string }[] }>("/staff/reviewers"), []);
  if (d.loading && !d.data) return <div className="space-y-4"><Skeleton className="h-10 w-80" /><div className="grid gap-4 lg:grid-cols-[1fr_22rem]"><Skeleton className="h-96" /><Skeleton className="h-96" /></div></div>;
  if (d.error && !d.data) {
    const e = d.error as ApiError;
    return <Alert tone="danger" title={e.status === 404 ? "Case not found" : e.status === 409 ? "Case unavailable" : "Could not load the case"} action={<Button variant="secondary" onClick={d.reload}>Retry</Button>}>{errMessage(e)}</Alert>;
  }
  const data = d.data!;
  const c = data.case;
  const onChanged = async () => { await d.reloadSilent(); };
  const unresolved = data.conflicts.filter((x) => x.status !== "reviewer_resolved").length;
  return (
    <div className="space-y-5">
      <div>
        <Link href="/staff" className="inline-flex items-center gap-1 text-sm text-ink-soft hover:text-ink"><ChevronLeft className="h-4 w-4" />Queues</Link>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-3xl font-semibold">{c.case_number}</h1>
          <QueueBadge queue={c.queue} />
          <Badge>{human(c.status)}</Badge>
          {c.is_synthetic && <Badge title="Synthetic demonstration data">synthetic</Badge>}
          {c.processing_state === "processing" && <Badge tone="info"><Spinner className="h-3 w-3" />processing</Badge>}
          {c.processing_state === "failed" && <Badge tone="danger">processing failed - routed to review</Badge>}
          {d.stale && <Badge tone="warn">Reconnecting…</Badge>}
        </div>
        <p className="mt-1 text-sm text-ink-soft">{c.patient_label} · {langLabel(c.language)} · waiting {waiting(c.waiting_minutes)} · {c.assigned_label ? `assigned to ${c.assigned_label}` : "unassigned"}</p>
        {c.emergency_flagged && <Alert tone="danger" className="mt-3" title="Emergency wording was shown to the patient">The patient described a potentially serious symptom while completing the form and was told to seek emergency help. Consider contacting them.</Alert>}
      </div>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          <Tabs<T> label="Case sections" value={tab} onChange={setTab} tabs={[
            { id: "overview", label: "Overview" }, { id: "evidence", label: "Evidence", count: data.messages.length + data.uploads.length }, { id: "facts", label: "Facts", count: data.facts.filter((f) => !f.superseded).length },
            { id: "vitals", label: "Vitals" }, { id: "conflicts", label: "Conflicts", count: unresolved, tone: "danger" }, { id: "questions", label: "Questions", count: data.questions.filter((q) => q.status === "open").length },
            { id: "note", label: "Clinical note" }, { id: "audit", label: "Audit" }]} />
          <div role="tabpanel">
            {tab === "overview" && <Overview d={data} />}
            {tab === "evidence" && <Evidence d={data} caseId={id} onChanged={onChanged} />}
            {tab === "facts" && <Facts d={data} caseId={id} onChanged={onChanged} />}
            {tab === "vitals" && <Vitals d={data} />}
            {tab === "conflicts" && <Conflicts d={data} caseId={id} onChanged={onChanged} />}
            {tab === "questions" && <Questions qs={data.questions} />}
            {tab === "note" && <NoteTab d={data} caseId={id} onChanged={onChanged} />}
            {tab === "audit" && <AuditTab d={data} />}
          </div>
          {data.decisions.length > 0 && (
            <section className="card p-4"><h2 className="text-lg font-semibold">Decision history (append-only)</h2>
              <ol className="mt-2 space-y-2 text-sm">{data.decisions.map((x) => <li key={x.id} className="rounded-lg border border-stone p-2"><div className="flex flex-wrap items-center gap-2"><Badge tone={x.action === "confirm" ? "success" : "warn"}>{x.action}</Badge><QueueBadge queue={x.proposed_queue} prefix="proposed" />→<QueueBadge queue={x.final_queue} /></div><p className="mt-1">“{x.reason}”</p><p className="text-xs text-ink-faint">{x.decided_by} · {new Date(x.decided_at).toLocaleString()}</p></li>)}</ol></section>)}
        </div>
        <DecisionPanel d={data} caseId={id} onChanged={onChanged} reviewers={rev.data?.reviewers ?? []} />
      </div>
    </div>
  );
}
export default function Page() {
  const { id } = useParams<{ id: string }>();
  return <RequireRole role="staff"><Shell wide><Review id={id} /></Shell></RequireRole>;
}
