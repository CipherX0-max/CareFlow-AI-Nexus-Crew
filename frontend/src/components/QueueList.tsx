"use client";
import { AlertTriangle, ChevronLeft, ChevronRight, Siren } from "lucide-react";
import Link from "next/link";
import type { CaseRow } from "@/lib/types";
import { human, waiting } from "@/lib/format";
import { Alert, Badge, Button, EmptyState, QueueBadge, Skeleton } from "./ui";

export function CaseRows({ rows, loading, error, onRetry, emptyHint }: { rows: CaseRow[] | null; loading: boolean; error: Error | null; onRetry: () => void; emptyHint?: string }) {
  if (loading && !rows) return <div className="space-y-2" aria-busy="true" aria-label="Loading cases">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full" />)}</div>;
  if (error && !rows) return <Alert tone="danger" title="Could not load cases" action={<Button variant="secondary" onClick={onRetry}>Retry</Button>}>{error.message}</Alert>;
  if (!rows || rows.length === 0) return <EmptyState title="No cases here" icon={<Siren className="h-8 w-8" />}>{emptyHint || "Nothing matches these filters. New submissions appear automatically."}</EmptyState>;
  return (
    <ul className="divide-y divide-stone overflow-hidden rounded-xl border border-stone bg-paper" aria-label="Cases">
      {rows.map((c) => (
        <li key={c.id}>
          <Link href={`/staff/cases/${c.id}`} className="grid gap-x-4 gap-y-1 px-4 py-3 hover:bg-teal-soft/40 sm:grid-cols-[11.5rem_1fr_10rem_6.5rem_9rem] sm:items-center">
            <div className="flex items-center gap-2"><span className="whitespace-nowrap font-mono text-sm font-medium">{c.case_number}</span>{c.is_synthetic && <Badge title="Synthetic demonstration data">synthetic</Badge>}</div>
            <div className="min-w-0">
              <div className="truncate text-[15px]">{c.chief_complaint || <span className="text-ink-faint">No description</span>}</div>
              <div className="truncate text-xs text-ink-faint">{c.patient_label || "Patient"} · {c.age != null ? `${c.age} years` : "age not provided"}{c.symptom_duration ? ` · ${c.symptom_duration}` : ""}{c.worsening ? " · worsening reported" : ""}</div>
              <div className="truncate text-xs text-ink-soft" title={c.reason}>{c.reason}{c.missing_count ? ` · ${c.missing_count} missing` : ""}{c.conflict_count ? ` · ${c.conflict_count} unresolved conflict(s)` : ""}</div>
              {Object.keys(c.vitals).length > 0 && <div className="truncate text-[11px] text-ink-faint">{Object.entries(c.vitals).map(([k, v]) => `${k.replace(/_/g, " ")} ${v.value ?? "?"}${v.value2 != null ? `/${v.value2}` : ""} ${v.unit ?? ""}`.trim()).join(" · ")}</div>}
              <div className="truncate text-[11px] text-ink-faint">{c.sources.map((x) => x === "voice" ? "voice" : x).join(" · ") || "No source recorded"}{c.processing_state === "processing" ? " · processing…" : ""}{c.processing_state === "failed" ? " · processing failed" : ""}</div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <QueueBadge queue={c.queue} />
              {c.reassessment_pending && <span title="New information changed the proposal since a staff decision" className="text-amber-clinical"><AlertTriangle className="h-4 w-4" aria-label="Changed since decision" /></span>}
              {c.emergency_flagged && <span title="Emergency wording was shown to the patient" className="text-crimson-clinical"><Siren className="h-4 w-4" aria-label="Emergency wording flagged" /></span>}
            </div>
            <div className="text-sm"><span className="text-ink-faint sm:hidden">Waiting </span>{waiting(c.waiting_minutes)}</div>
            <div className="text-sm text-ink-soft"><div>{human(c.status)}</div><div className="truncate text-xs">{c.assigned_label || "Unassigned"}</div></div>
          </Link>
        </li>
      ))}
    </ul>
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
