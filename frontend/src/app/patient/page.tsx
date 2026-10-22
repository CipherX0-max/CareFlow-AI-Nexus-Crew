"use client";
import { FilePlus2, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Shell } from "@/components/Shell";
import { Alert, Badge, Button, EmptyState, Skeleton, useAsync, useToast } from "@/components/ui";
import { api, errMessage } from "@/lib/api";
import { RequireRole, useAuth } from "@/lib/auth";
import { when } from "@/lib/format";
import type { PatientCaseRow, PublicConfig } from "@/lib/types";

function Consent({ config, onDone }: { config: PublicConfig; onDone: () => void }) {
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const accept = async () => {
    setBusy(true); setError("");
    try { await api("/patient/consent", { json: { accept: true, text_version: config.consent.version } }); onDone(); } catch (e) { setError(errMessage(e)); } finally { setBusy(false); }
  };
  return (
    <section className="card mx-auto max-w-2xl p-6" aria-labelledby="consent-h">
      <div className="flex items-center gap-2 text-teal"><ShieldCheck className="h-5 w-5" /><span className="text-sm font-medium">Before you start</span></div>
      <h1 id="consent-h" className="mt-1 text-2xl font-semibold">{config.consent.title}</h1>
      <dl className="mt-4 space-y-3">{config.consent.sections.map((s) => <div key={s.heading}><dt className="text-sm font-semibold">{s.heading}</dt><dd className="text-sm text-ink-soft">{s.body}</dd></div>)}</dl>
      {error && <Alert tone="danger" className="mt-4">{error}</Alert>}
      <label className="mt-5 flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={ok} onChange={(e) => setOk(e.target.checked)} />I have read this and agree that my information may be processed as described.</label>
      <Button className="mt-4" onClick={accept} loading={busy} disabled={!ok}>Agree and continue</Button>
    </section>
  );
}

function Home() {
  const { me, refresh } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const config = useAsync(() => api<PublicConfig>("/config/public"), []);
  const consented = !!me?.consent.granted && me.consent.text_version === config.data?.consent.version;
  const cases = useAsync(() => (consented ? api<{ cases: PatientCaseRow[] }>("/cases") : Promise.resolve({ cases: [] })), [consented]);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try { const c = await api<{ id: string }>("/cases", { method: "POST", json: {} }); router.push(`/patient/cases/${c.id}`); }
    catch (e) { toast(errMessage(e), "danger"); setBusy(false); }
  };
  if (config.loading) return <Skeleton className="h-48 w-full" />;
  if (config.error || !config.data) return <Alert tone="danger" action={<Button variant="secondary" onClick={config.reload}>Retry</Button>}>Could not load the service configuration. {errMessage(config.error)}</Alert>;
  if (!consented) return <Consent config={config.data} onDone={async () => { await refresh(); }} />;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-3xl font-semibold">Your cases</h1><p className="text-ink-soft">Start a new intake or continue one you began.</p></div>
        <Button onClick={start} loading={busy}><FilePlus2 className="h-4 w-4" />Start a new intake</Button>
      </div>
      {config.data.demonstration_rules && <Alert tone="warn" title="Demonstration mode">Use made-up information only. The prioritisation rules are unreviewed placeholders.</Alert>}
      {cases.loading ? <div className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>
        : cases.error ? <Alert tone="danger" action={<Button variant="secondary" onClick={cases.reload}>Retry</Button>}>Could not load your cases. {errMessage(cases.error)}</Alert>
        : cases.data!.cases.length === 0 ? <EmptyState title="No cases yet" action={<Button onClick={start} loading={busy}>Start your first intake</Button>}>Tell us what is wrong in your own words. It takes a few minutes, and you control what is submitted.</EmptyState>
        : <ul className="space-y-3">{cases.data!.cases.map((c) => (
          <li key={c.id}><Link href={`/patient/cases/${c.id}`} className="card block p-4 transition-colors hover:border-teal">
            <div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm text-ink-soft">{c.case_number}</span><Badge tone={c.status === "draft" ? "neutral" : c.status === "awaiting_patient" ? "warn" : "info"}>{c.status_label}</Badge>
              {c.open_questions > 0 && c.status !== "deletion_requested" && <Badge tone="warn">{c.open_questions} question{c.open_questions > 1 ? "s" : ""} for you</Badge>}</div>
            <p className="mt-1 line-clamp-2 text-ink">{c.chief_complaint || "No description yet"}</p>
            <p className="mt-1 text-xs text-ink-faint">Started {when(c.created_at)}{c.submitted_at ? ` · Submitted ${when(c.submitted_at)}` : ""}</p>
          </Link></li>))}</ul>}
      <div className="border-t border-stone pt-4 text-sm">
        <button className="text-ink-soft underline" onClick={async () => { if (confirm("Withdraw consent? You will not be able to start or change cases until you agree again.")) { try { await api("/patient/consent/withdraw", { json: {} }); await refresh(); } catch (e) { toast(errMessage(e), "danger"); } } }}>Withdraw consent</button>
      </div>
    </div>
  );
}
export default function PatientHome() { return <RequireRole role="patient"><Shell><Home /></Shell></RequireRole>; }
