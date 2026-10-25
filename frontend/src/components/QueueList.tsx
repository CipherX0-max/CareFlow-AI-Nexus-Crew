"use client";
import { AlertTriangle, ChevronLeft, ChevronRight, Siren } from "lucide-react";
import Link from "next/link";
import type { CaseRow } from "@/lib/types";
import { human, waiting, when } from "@/lib/format";
import { Alert, Badge, Button, EmptyState, QueueBadge, Skeleton, cx } from "./ui";

export function CaseRows({ rows, loading, error, onRetry, emptyHint, basePath = "/staff" }: { rows: CaseRow[] | null; loading: boolean; error: Error | null; onRetry: () => void; emptyHint?: string; basePath?: "/staff" | "/doctor" }) {
  if (loading && !rows) return <div className="space-y-2" aria-busy="true" aria-label="Loading cases">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full" />)}</div>;
  if (error && !rows) return <Alert tone="danger" title="Could not load cases" action={<Button variant="secondary" onClick={onRetry}>Retry</Button>}>{error.message}</Alert>;
  if (!rows || rows.length === 0) return <EmptyState title="No cases here" icon={<Siren className="h-8 w-8" />}>{emptyHint || "Nothing matches these filters. New submissions appear automatically."}</EmptyState>;
  return (
    <div className="overflow-hidden rounded-xl border border-stone bg-paper">
    <div className="hidden grid-cols-[12.5rem_minmax(0,1fr)_10.5rem_7.5rem_9rem] gap-x-4 border-b border-stone bg-ivory px-5 py-2 font-mono text-[10.5px] uppercase tracking-[0.12em] text-ink-faint lg:grid" aria-hidden="true">
      <span>Reference</span><span>Summary and evidence</span><span>Priority</span><span>Waiting</span><span>Status and owner</span>
    </div>
    <ul className="divide-y divide-stone" aria-label="Cases">
      {rows.map((c, idx) => {
        const isEmergency = c.urgency === "EMERGENCY" || c.emergency_flagged || c.escalation_level === "emergency" || c.escalation_level === "EMERGENCY";
        const catRank = c.category_rank ?? c.queue_rank ?? (idx + 1);
        const explainText = c.ranking_explanation || c.reason;
        return (
          <li key={c.id} className={cx("border-l-[3px] transition-colors", isEmergency ? "border-l-crimson-clinical bg-crimson-soft/50" : c.queue === "URGENT" ? "border-l-crimson-clinical" : c.queue === "NEEDS_REVIEW" ? "border-l-amber-clinical" : c.queue === "NORMAL" ? "border-l-moss" : "border-l-stone-dark")}>
            <Link href={`${basePath}/cases/${c.id}`} className="grid gap-x-4 gap-y-1.5 px-4 py-3.5 transition-colors hover:bg-butter-soft/45 focus-visible:bg-butter-soft/45 sm:px-5 lg:grid-cols-[12.5rem_minmax(0,1fr)_10.5rem_7.5rem_9rem] lg:items-center">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="inline-flex h-6 min-w-[2rem] items-center justify-center rounded bg-slate-soft px-1.5 font-mono text-xs font-medium text-ink" aria-label={`Priority rank #${catRank}`}>#{catRank}</span>
                <span className="whitespace-nowrap font-mono text-sm font-medium">{c.case_number}</span>
                {c.is_synthetic && <Badge title="Synthetic demonstration data">synthetic</Badge>}
                {isEmergency && <Badge tone="danger">EMERGENCY</Badge>}
              </div>
              <div className="min-w-0">
                <div className="truncate text-[15px] font-semibold leading-snug">{c.chief_complaint || <span className="text-ink-faint">No description</span>}</div>
                <div className="truncate text-xs text-ink-faint">{c.patient_label || "Patient"} · {c.age != null ? `${c.age} years` : "age not provided"}{c.contact_mobile ? ` · ${c.contact_mobile}` : ""}{c.symptom_duration ? ` · ${c.symptom_duration}` : ""}{c.worsening ? " · worsening reported" : ""}</div>
                {c.red_flags && c.red_flags.length > 0 && (
                  <div className="mt-0.5 flex flex-wrap gap-1">{c.red_flags.map((rf, i) => <Badge key={i} tone="danger" className="text-[11px]">{rf}</Badge>)}</div>
                )}
                <div className="truncate text-xs text-ink-soft" title={explainText}>{explainText}{c.missing_count ? ` · ${c.missing_count} missing` : ""}{c.conflict_count ? ` · ${c.conflict_count} unresolved conflict(s)` : ""}</div>
                {Object.keys(c.vitals).length > 0 && <div className="truncate text-[11px] text-ink-faint">{Object.entries(c.vitals).map(([k, v]) => `${k.replace(/_/g, " ")} ${v.value ?? "?"}${v.value2 != null ? `/${v.value2}` : ""} ${v.unit ?? ""}`.trim()).join(" · ")}</div>}
                <div className="truncate text-[11px] text-ink-faint">{c.sources.map((x) => x === "voice" ? "voice" : x).join(" · ") || "No source recorded"}{c.processing_state === "processing" ? " · processing…" : ""}{c.processing_state === "failed" ? " · processing failed" : ""}</div>
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <QueueBadge queue={c.queue} />
                {c.needs_review && c.queue !== "NEEDS_REVIEW" && <Badge tone="warn">Needs review</Badge>}
                {c.review_requested && <Badge tone="warn">Doctor review requested</Badge>}
                {c.reassessment_pending && <span title="New information changed the proposal since a staff decision" className="text-amber-clinical"><AlertTriangle className="h-4 w-4" aria-label="Changed since decision" /></span>}
                {c.emergency_flagged && <span title="Emergency wording was shown to the patient" className="text-crimson-clinical"><Siren className="h-4 w-4" aria-label="Emergency wording flagged" /></span>}
              </div>
              <div className="text-sm">
                <div><span className="text-ink-faint sm:hidden">Waiting </span>{waiting(c.waiting_minutes)}</div>
                {c.submitted_at && <div className="truncate text-[11px] text-ink-faint" title="Time received">{when(c.submitted_at)}</div>}
              </div>
              <div className="text-sm text-ink-soft"><div className="capitalize">{human(c.status)}</div><div className="truncate text-xs">{c.assigned_label || "Unassigned"}</div></div>
            </Link>
          </li>
        );
      })}
    </ul>
    </div>
  );
}

export function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return <p className="text-xs text-ink-faint">{total} case{total === 1 ? "" : "s"}</p>;
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-ink-faint">{total} cases · page {page} of {pages}</span>
      <div className="flex gap-2">
        <Button variant="secondary" onClick={() => onPage(page - 1)} disabled={page <= 1} aria-label="Previous page"><ChevronLeft className="h-4 w-4" /></Button>
        <Button variant="secondary" onClick={() => onPage(page + 1)} disabled={page >= pages} aria-label="Next page"><ChevronRight className="h-4 w-4" /></Button>
      </div>
    </div>
  );
}
