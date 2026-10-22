"use client";
import { CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import { Shell } from "@/components/Shell";
import { Alert, Badge, Button, Dialog, EmptyState, Field, Skeleton, Tabs, useAsync, useToast } from "@/components/ui";
import { api, errMessage } from "@/lib/api";
import { RequireRole, useAuth } from "@/lib/auth";
import { when } from "@/lib/format";
import type { AdminUser, SystemInfo } from "@/lib/types";

type T = "accounts" | "audit" | "system" | "deletions";

function Accounts() {
  const toast = useToast();
  const { user: me } = useAuth();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const list = useAsync(() => api<{ users: AdminUser[]; total: number }>(`/admin/users?page_size=100${role ? `&role=${role}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`), [role, q]);
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ email: "", display_name: "", role: "staff" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [created, setCreated] = useState<{ email: string; pw: string } | null>(null);
  const create = async () => {
    setBusy(true); setErr("");
    try { const r = await api("/admin/users", { json: f }); setOpen(false); setCreated({ email: r.user.email, pw: r.temporary_password }); setF({ email: "", display_name: "", role: "staff" }); list.reload(); }
    catch (e) { setErr(errMessage(e)); } finally { setBusy(false); }
  };
  const patch = async (u: AdminUser, body: object) => { try { await api(`/admin/users/${u.id}`, { method: "PATCH", json: body }); list.reload(); } catch (e) { toast(errMessage(e), "danger"); } };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input className="input max-w-xs" placeholder="Search name or email" aria-label="Search users" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input max-w-[10rem]" aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}><option value="">All roles</option><option value="patient">Patients</option><option value="staff">Staff</option><option value="admin">Admins</option></select>
        <Button className="ml-auto" onClick={() => setOpen(true)}>Create staff / admin account</Button>
      </div>
      <Alert tone="info">Public sign-up only ever creates patients. Staff and administrator accounts exist only because an administrator created them here. Administrators cannot open case contents.</Alert>
      {created && <Alert tone="warn" title="Temporary password - shown once" action={<Button variant="ghost" onClick={() => setCreated(null)}>Dismiss</Button>}>Share it securely with {created.email}: <code className="rounded bg-paper px-1.5 py-0.5 font-mono">{created.pw}</code>. They must change it at first sign-in.</Alert>}
      {list.loading && !list.data ? <Skeleton className="h-48" /> : list.error && !list.data ? <Alert tone="danger" action={<Button variant="secondary" onClick={list.reload}>Retry</Button>}>{errMessage(list.error)}</Alert>
        : list.data!.users.length === 0 ? <EmptyState title="No users match">Try a different search.</EmptyState>
        : <div className="overflow-x-auto rounded-xl border border-stone bg-paper"><table className="w-full min-w-[720px] text-sm"><thead className="bg-slate-soft text-left text-xs text-ink-soft"><tr><th className="p-2">Name</th><th className="p-2">Email</th><th className="p-2">Role</th><th className="p-2">Status</th><th className="p-2">Created</th><th className="p-2" /></tr></thead>
          <tbody className="divide-y divide-stone">{list.data!.users.map((u) => (
            <tr key={u.id}><td className="p-2 font-medium">{u.display_name}{u.is_synthetic && <Badge className="ml-2">synthetic</Badge>}</td><td className="p-2">{u.email}</td>
              <td className="p-2">{u.role === "patient" ? <Badge>patient</Badge> : <select className="input !py-1" aria-label={`Role of ${u.email}`} value={u.role} disabled={u.id === me?.id} onChange={(e) => patch(u, { role: e.target.value })}><option value="staff">staff</option><option value="admin">admin</option></select>}</td>
              <td className="p-2">{u.is_active ? <Badge tone="success">active</Badge> : <Badge tone="danger">disabled</Badge>}{u.locked && <Badge tone="warn" className="ml-1">locked</Badge>}</td><td className="p-2 text-ink-faint">{when(u.created_at)}</td>
              <td className="p-2 text-right">{u.id !== me?.id && <Button variant="ghost" onClick={() => patch(u, { is_active: !u.is_active })}>{u.is_active ? "Disable" : "Enable"}</Button>}</td></tr>))}</tbody></table></div>}
      <Dialog open={open} onClose={() => setOpen(false)} title="Create staff or admin account" footer={<><Button variant="secondary" onClick={() => setOpen(false)}>Cancel</Button><Button onClick={create} loading={busy} disabled={!f.email || !f.display_name}>Create</Button></>}>
        <div className="space-y-3">{err && <Alert tone="danger">{err}</Alert>}
          <Field label="Name" htmlFor="n"><input id="n" className="input" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
          <Field label="Email" htmlFor="e"><input id="e" type="email" className="input" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Role" htmlFor="r"><select id="r" className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="staff">Staff (review cases and decide)</option><option value="admin">Administrator (accounts, audit, system)</option></select></Field></div>
      </Dialog>
    </div>
  );
}

function Audit() {
  const [action, setAction] = useState("");
  const [page, setPage] = useState(1);
  const l = useAsync(() => api<{ events: any[]; total: number }>(`/admin/audit?page=${page}&page_size=50${action ? `&action=${encodeURIComponent(action)}` : ""}`), [action, page]);
  const [chain, setChain] = useState<{ ok: boolean; checked: number; broken_at?: number } | null>(null);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <select className="input max-w-xs" aria-label="Action filter" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}>
          <option value="">All actions</option>{["auth.", "case.", "decision.", "conflict.", "note.", "upload.", "admin.", "assessment."].map((a) => <option key={a} value={a}>{a}*</option>)}</select>
        <Button variant="secondary" onClick={async () => setChain(await api("/admin/audit/verify"))}><ShieldCheck className="h-4 w-4" />Verify integrity chain</Button>
        {chain && (chain.ok ? <Badge tone="success"><CheckCircle2 className="h-3 w-3" />chain intact · {chain.checked} events</Badge> : <Badge tone="danger"><XCircle className="h-3 w-3" />broken at event {chain.broken_at}</Badge>)}
      </div>
      <p className="text-xs text-ink-faint">The audit log is append-only (database triggers block edits and deletes) and hash-chained. It stores identifiers and decisions, not clinical text, passwords or tokens.</p>
      {l.loading && !l.data ? <Skeleton className="h-64" /> : l.error && !l.data ? <Alert tone="danger" action={<Button variant="secondary" onClick={l.reload}>Retry</Button>}>{errMessage(l.error)}</Alert>
        : <div className="overflow-x-auto rounded-xl border border-stone bg-paper"><table className="w-full min-w-[760px] text-sm"><thead className="bg-slate-soft text-left text-xs text-ink-soft"><tr><th className="p-2">#</th><th className="p-2">Time</th><th className="p-2">Actor</th><th className="p-2">Action</th><th className="p-2">Details</th></tr></thead>
          <tbody className="divide-y divide-stone">{l.data!.events.map((e) => <tr key={e.id}><td className="p-2 text-ink-faint">{e.id}</td><td className="p-2 whitespace-nowrap">{when(e.ts)}</td><td className="p-2">{e.actor}<div className="text-xs text-ink-faint">{e.role}</div></td><td className="p-2 font-mono text-xs">{e.action}</td>
            <td className="max-w-md break-words p-2 text-xs text-ink-soft">{Object.entries(e.details || {}).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(",") : String(v)}`).join(" · ")}{e.case_id && <div className="text-ink-faint">case {e.case_id.slice(0, 8)}</div>}</td></tr>)}</tbody></table></div>}
      {l.data && <div className="flex items-center justify-between text-sm"><span className="text-ink-faint">{l.data.total} events</span><div className="flex gap-2"><Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Newer</Button><Button variant="secondary" disabled={page * 50 >= l.data.total} onClick={() => setPage(page + 1)}>Older</Button></div></div>}
    </div>
  );
}

function Status({ ok, children }: { ok: boolean; children: React.ReactNode }) { return <Badge tone={ok ? "success" : "danger"}>{ok ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}{children}</Badge>; }
function System() {
  const s = useAsync(() => api<SystemInfo>("/admin/system"), [], { poll: 15000 });
  if (s.loading && !s.data) return <Skeleton className="h-64" />;
  if (s.error && !s.data) return <Alert tone="danger" action={<Button variant="secondary" onClick={s.reload}>Retry</Button>}>{errMessage(s.error)}</Alert>;
  const d = s.data!;
  const rows: [string, boolean, string][] = [["Database", d.database.available, d.database.detail], ["Local LLM (Ollama)", d.llm.available, `${d.llm.model}: ${d.llm.detail}`], ["Speech-to-text (faster-whisper)", d.speech.available, `${d.speech.model}: ${d.speech.detail}`], ["OCR (Tesseract)", d.ocr.available, d.ocr.detail]];
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2">{rows.map(([n, ok, det]) => <div key={n} className="card p-4"><div className="flex items-center justify-between"><h3 className="font-semibold">{n}</h3><Status ok={ok}>{ok ? "available" : "unavailable"}</Status></div><p className="mt-1 text-sm text-ink-soft">{det}</p>
        {!ok && <p className="mt-1 text-xs text-amber-clinical">Cases are never silently treated as low-risk when a component is down; they route to Needs review.</p>}</div>)}</div>
      <section className="card p-4">
        <div className="flex flex-wrap items-center gap-2"><h3 className="text-lg font-semibold">Active ruleset</h3><Badge>{d.ruleset.version}</Badge>{d.ruleset.clinically_approved ? <Badge tone="success">clinician-approved</Badge> : <Badge tone="warn">DEMONSTRATION - not clinically reviewed</Badge>}</div>
        <p className="mt-2 text-sm text-ink-soft">{d.ruleset.note}</p>
        <table className="mt-3 w-full text-sm"><thead className="text-left text-xs text-ink-soft"><tr><th className="py-1">Rule</th><th>Name</th><th>Tier</th></tr></thead><tbody className="divide-y divide-stone">{d.ruleset.urgent_rules.map((r) => <tr key={r.id}><td className="py-1 font-mono">{r.id}</td><td>{r.name}{r.placeholder && <Badge tone="warn" className="ml-2">placeholder number</Badge>}</td><td>{r.tier}</td></tr>)}</tbody></table>
        <p className="mt-3 text-xs text-ink-faint"><b>Ordering policy:</b> {d.ruleset.ordering_policy}</p>
        <p className="mt-1 text-xs text-ink-faint"><b>Safety-critical concepts:</b> {d.ruleset.safety_critical_concepts.join(", ")}</p>
      </section>
      <section className="card p-4" aria-labelledby="knowledge-heading">
        <div className="flex flex-wrap items-center gap-2"><h3 id="knowledge-heading" className="text-lg font-semibold">Clinical knowledge registry</h3>{d.knowledge.available ? <Badge tone={d.knowledge.clinically_approved ? "success" : "warn"}>{d.knowledge.review_status}</Badge> : <Badge tone="danger">unavailable</Badge>}</div>
        {d.knowledge.available ? <>
          <p className="mt-2 text-sm text-ink-soft">{d.knowledge.purpose}</p>
          <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <div><dt className="text-xs text-ink-faint">Catalog version</dt><dd>{d.knowledge.version}</dd></div>
            <div><dt className="text-xs text-ink-faint">Internal concepts</dt><dd>{d.knowledge.concept_count} · external mappings {d.knowledge.external_mappings}</dd></div>
            <div><dt className="text-xs text-ink-faint">Terminology</dt><dd>{d.knowledge.terminology?.namespace} · SNOMED CT {d.knowledge.terminology?.snomed_ct.status} (licence {d.knowledge.terminology?.snomed_ct.licence_review})</dd></div>
            <div><dt className="text-xs text-ink-faint">Protocol linkage</dt><dd>{d.knowledge.matches_active_ruleset && d.knowledge.rule_ids_match ? "Matches active ruleset" : "Does not match active ruleset"}</dd></div>
            <div><dt className="text-xs text-ink-faint">Clinical source</dt><dd>{d.knowledge.source_organisation || "Not supplied"}{d.knowledge.source_reference ? ` · ${d.knowledge.source_reference}` : ""}</dd></div>
            <div><dt className="text-xs text-ink-faint">Effective date</dt><dd>{d.knowledge.effective_date || "Not set"}</dd></div>
          </dl>
          <Alert tone="warn" className="mt-3">{d.knowledge.note} A terminology registry is not a triage protocol.</Alert>
        </> : <Alert tone="danger" className="mt-3">The configured knowledge registry could not be loaded ({d.knowledge.detail}).</Alert>}
      </section>
      <section className="card p-4"><h3 className="text-lg font-semibold">Configuration</h3>
        <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2"><div className="flex justify-between"><dt className="text-ink-soft">Environment</dt><dd>{d.environment}</dd></div><div className="flex justify-between"><dt className="text-ink-soft">Secure cookies</dt><dd>{String(d.cookie_secure)}</dd></div>
          <div className="flex justify-between"><dt className="text-ink-soft">Dev tokens returned in API</dt><dd>{String(d.expose_dev_tokens)}</dd></div>{Object.entries(d.limits).map(([k, v]) => <div key={k} className="flex justify-between"><dt className="text-ink-soft">{k.replace(/_/g, " ")}</dt><dd>{v}</dd></div>)}
          <div className="flex justify-between"><dt className="text-ink-soft">Cases / users</dt><dd>{d.counts.cases} / {d.counts.users}</dd></div></dl>
        {d.expose_dev_tokens && <Alert tone="warn" className="mt-3">Development mode: password-reset and verification tokens are returned by the API because no email service is configured. Disable before any shared deployment.</Alert>}</section>
    </div>
  );
}

function Deletions() {
  const toast = useToast();
  const l = useAsync(() => api<{ requests: { id: string; case_number: string; requested_at: string; retention_hold: boolean; hold_reason: string | null }[] }>("/admin/deletion-requests"), []);
  const [t, setT] = useState<{ id: string; case_number: string; retention_hold: boolean } | null>(null);
  const [confirm, setConfirm] = useState("");
  const [over, setOver] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!t) return; setBusy(true);
    try { await api(`/admin/cases/${t.id}/purge`, { json: { confirm_case_number: confirm, override_retention_reason: over } }); toast("Case contents deleted. The audit trail is kept."); setT(null); setConfirm(""); setOver(""); l.reload(); }
    catch (e) { toast(errMessage(e), "danger"); } finally { setBusy(false); }
  };
  if (l.loading && !l.data) return <Skeleton className="h-32" />;
  if (l.error && !l.data) return <Alert tone="danger" action={<Button variant="secondary" onClick={l.reload}>Retry</Button>}>{errMessage(l.error)}</Alert>;
  if (!l.data!.requests.length) return <EmptyState title="No pending deletion requests">Patients can request deletion of their cases. Requests appear here.</EmptyState>;
  return (
    <div className="space-y-3">{l.data!.requests.map((r) => (
      <div key={r.id} className="card flex flex-wrap items-center gap-3 p-3"><span className="font-mono">{r.case_number}</span><span className="text-sm text-ink-faint">requested {when(r.requested_at)}</span>{r.retention_hold && <Badge tone="warn" title={r.hold_reason ?? ""}>retention hold</Badge>}<Button className="ml-auto" variant="danger" onClick={() => setT(r)}>Fulfil deletion…</Button></div>))}
      <Dialog open={!!t} onClose={() => setT(null)} title={`Delete contents of ${t?.case_number}`} footer={<><Button variant="secondary" onClick={() => setT(null)}>Cancel</Button><Button variant="danger" loading={busy} disabled={confirm !== t?.case_number || (!!t?.retention_hold && over.trim().length < 15)} onClick={run}>Delete permanently</Button></>}>
        <p className="mb-3 text-sm">This permanently removes messages, uploads, extracted facts and notes. The case shell and the audit trail are kept. This cannot be undone.</p>
        <Field label={`Type ${t?.case_number} to confirm`} htmlFor="c"><input id="c" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Field>
        {t?.retention_hold && <div className="mt-3"><Alert tone="warn" className="mb-2">An approved clinical note exists, so a retention policy may apply. Deletion needs a documented override reason.</Alert><Field label="Retention override reason (15+ chars)" htmlFor="o"><textarea id="o" className="input" value={over} onChange={(e) => setOver(e.target.value)} /></Field></div>}
      </Dialog>
    </div>
  );
}

function Admin() {
  const [tab, setTab] = useState<T>("accounts");
  return (
    <div className="space-y-5">
      <div><h1 className="text-3xl font-semibold">Administration</h1><p className="text-sm text-ink-soft">Accounts, audit trail and system health. Case contents are deliberately not visible here.</p></div>
      <Tabs<T> label="Administration" value={tab} onChange={setTab} tabs={[{ id: "accounts", label: "Accounts" }, { id: "audit", label: "Audit log" }, { id: "system", label: "System and rules" }, { id: "deletions", label: "Deletion requests" }]} />
      <div role="tabpanel">{tab === "accounts" && <Accounts />}{tab === "audit" && <Audit />}{tab === "system" && <System />}{tab === "deletions" && <Deletions />}</div>
    </div>
  );
}
export default function AdminPage() { return <RequireRole role="admin"><Shell wide><Admin /></Shell></RequireRole>; }
