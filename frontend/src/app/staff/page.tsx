"use client";
import { RefreshCw, Search, WifiOff } from "lucide-react";
import { useEffect, useState } from "react";
import { CaseRows, Pager } from "@/components/QueueList";
import { Shell } from "@/components/Shell";
import { Alert, Button, cx, useAsync } from "@/components/ui";
import { api } from "@/lib/api";
import { RequireRole } from "@/lib/auth";
import type { CaseRow, Queue, QueueSummary } from "@/lib/types";

type Tab = Queue | "ALL";
const TABS: { id: Tab; label: string; hint: string; tone: string }[] = [
  { id: "URGENT", label: "Urgent", hint: "Configured urgent rule matched", tone: "border-crimson-clinical" },
  { id: "NEEDS_REVIEW", label: "Needs review", hint: "Insufficient, conflicting or unreliable information", tone: "border-amber-clinical" },
  { id: "NORMAL", label: "Normal", hint: "No urgent rule matched; information complete", tone: "border-moss" },
  { id: "ALL", label: "All open", hint: "Every open case, in queue order", tone: "border-ink" },
];

function Dashboard() {
  const [tab, setTab] = useState<Tab>("URGENT");
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [assigned, setAssigned] = useState("");
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 15;
  useEffect(() => { const t = setTimeout(() => { setDq(q.trim()); setPage(1); }, 350); return () => clearTimeout(t); }, [q]);

  const summary = useAsync(() => api<QueueSummary>("/staff/queues/summary"), [], { poll: 5000 });
  const reviewers = useAsync(() => api<{ reviewers: { id: string; name: string }[] }>("/staff/reviewers"), []);
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (tab !== "ALL") params.set("queue", tab);
  if (dq) params.set("q", dq);
  if (assigned) params.set("assigned", assigned);
  if (status) params.set("status", status);
  if (source) params.set("source", source);
  if (from) params.set("date_from", new Date(from).toISOString());
  if (to) params.set("date_to", new Date(to + "T23:59:59").toISOString());
  const list = useAsync(() => api<{ cases: CaseRow[]; total: number }>(`/staff/cases?${params}`), [tab, dq, assigned, status, source, from, to, page]);
  // refetch the list only when something actually changed (cheap summary poll carries a version counter)
  const version = summary.data?.version;
  useEffect(() => { if (version !== undefined) list.reloadSilent(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version]);
  const counts = summary.data?.counts;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="page-title">Staff Intake & Coordination</h1>
          <p className="text-sm text-ink-soft">Operational queue: review intake warnings, ask clarifying questions, escalate emergencies, and route cases to doctors.</p></div>
        <div className="flex items-center gap-2 text-xs text-ink-faint">
          {summary.stale ? <span className="inline-flex items-center gap-1 text-amber-clinical"><WifiOff className="h-3.5 w-3.5" />Reconnecting - showing last known data</span> : <span>Live · updates every 5 s</span>}
          <Button variant="ghost" onClick={() => { summary.reload(); list.reload(); }} aria-label="Refresh now"><RefreshCw className="h-4 w-4" /></Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" role="tablist" aria-label="Queues">
        {TABS.map((t) => {
          const n = t.id === "ALL" ? summary.data?.total_open : counts?.[t.id as Queue];
          return (
            <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => { setTab(t.id); setPage(1); }}
              className={cx("card border-l-4 p-4 text-left transition-colors", t.tone, tab === t.id ? "bg-butter-soft/55 ring-2 ring-forest/35" : "hover:bg-paper/80")}>
              <div className="text-sm font-medium text-ink-soft">{t.label}</div>
              <div className="font-display text-4xl font-semibold leading-none tabular-nums" aria-label={`${t.label}: ${n ?? "loading"}`}>{n ?? <span className="skeleton inline-block h-7 w-10 align-middle" />}</div>
              <div className="mt-0.5 hidden text-xs text-ink-faint sm:block">{t.hint}</div>
            </button>
          );
        })}
      </div>
      {summary.data && <section className="flex flex-wrap items-center gap-x-5 gap-y-2 border-y border-stone py-3 text-xs text-ink-soft" aria-label="Workflow status counts">
        <span className="font-semibold text-ink">{summary.data.total_cases} submitted case(s)</span>
        {[["Submitted", summary.data.workflow_counts.submitted], ["In review", summary.data.workflow_counts.in_review], ["Awaiting patient", summary.data.workflow_counts.awaiting_patient], ["Resolved", summary.data.workflow_counts.resolved]].map(([label, n]) => <span key={String(label)}>{label}: {n}</span>)}
      </section>}
      {summary.data && summary.data.reassessment_pending > 0 && <Alert tone="warn">{summary.data.reassessment_pending} case(s) changed since a staff decision and need re-confirmation (marked with ⚠).</Alert>}

      <div className="card grid gap-3 p-3 sm:grid-cols-2 lg:grid-cols-6">
        <div className="relative lg:col-span-2"><Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-ink-faint" aria-hidden />
          <input className="input pl-9" placeholder="Search case, patient or complaint" aria-label="Search cases" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <select className="input" aria-label="Reviewer" value={assigned} onChange={(e) => { setAssigned(e.target.value); setPage(1); }}>
          <option value="">Any reviewer</option><option value="me">Assigned to me</option><option value="unassigned">Unassigned</option>
          {reviewers.data?.reviewers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select>
        <select className="input" aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Open cases</option><option value="submitted">Submitted</option><option value="in_review">In review</option><option value="awaiting_patient">Awaiting patient</option><option value="resolved">Resolved</option></select>
        <select className="input" aria-label="Input source" value={source} onChange={(e) => { setSource(e.target.value); setPage(1); }}>
          <option value="">Any source</option><option value="text">Typed text</option><option value="voice">Voice</option><option value="document">Document</option></select>
        <input type="date" className="input" aria-label="Submitted from" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} />
        <input type="date" className="input" aria-label="Submitted to" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} />
      </div>

      <CaseRows rows={list.data?.cases ?? null} loading={list.loading} error={list.error} onRetry={list.reload}
        emptyHint={tab === "URGENT" ? "No urgent cases right now. New submissions appear automatically." : undefined} />
      {list.data && <Pager page={page} pageSize={pageSize} total={list.data.total} onPage={setPage} />}
    </div>
  );
}
export default function StaffHome() { return <RequireRole role="staff"><Shell wide><Dashboard /></Shell></RequireRole>; }
