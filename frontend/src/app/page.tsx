"use client";
import {
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  Cpu,
  FileText,
  Menu,
  Mic,
  ScrollText,
  ShieldCheck,
  UserCheck,
  Volume2,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Brand } from "@/components/Shell";
import { Reveal } from "@/components/Reveal";
import { QueueBadge, cx } from "@/components/ui";
import { homeFor, useAuth } from "@/lib/auth";
import type { Queue } from "@/lib/types";

const SECTIONS = [
  { href: "#how-it-works", label: "Workflow" },
  { href: "#queue", label: "Review queue" },
  { href: "#privacy", label: "Local architecture" },
];

/** Illustrative rows only. Everything below this line that is not a route link is synthetic demonstration content. */
const SAMPLE_ROWS: {
  rank: number;
  ref: string;
  summary: string;
  detail: string;
  queue: Queue;
  wait: string;
  status: string;
  note?: string;
}[] = [
  {
    rank: 1,
    ref: "CF-7A31D0E2",
    summary: "Chest pain for 2 days, shortness of breath",
    detail: "Hinglish voice · Lab PDF · age 52",
    queue: "URGENT",
    wait: "12 min",
    status: "Submitted",
    note: "Rule DEMO-U4 matched",
  },
  {
    rank: 2,
    ref: "CF-19C4B8F6",
    summary: "Facial droop and slurred speech since morning",
    detail: "English text · age 66",
    queue: "URGENT",
    wait: "34 min",
    status: "In review",
    note: "Rule DEMO-U1 matched",
  },
  {
    rank: 1,
    ref: "CF-52E0A9D3",
    summary: "High fever; age and symptom duration not yet provided",
    detail: "English text",
    queue: "NEEDS_REVIEW",
    wait: "1 h 05 min",
    status: "Awaiting patient",
    note: "2 missing details",
  },
  {
    rank: 2,
    ref: "CF-C08F6217",
    summary: "Bengali voice note with low transcription confidence",
    detail: "Voice · original transcript preserved",
    queue: "NEEDS_REVIEW",
    wait: "1 h 40 min",
    status: "Submitted",
    note: "Transcript flagged",
  },
  {
    rank: 1,
    ref: "CF-3B7D45A1",
    summary: "Mild sore throat for 3 days, denies fever or dyspnea",
    detail: "English text · age 30",
    queue: "NORMAL",
    wait: "2 h 10 min",
    status: "Submitted",
  },
];

function PrimaryLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="group inline-flex min-h-[50px] items-center gap-3 rounded-lg bg-forest px-6 text-[15px] font-semibold text-white shadow-card transition-[background-color,transform] duration-150 hover:bg-forest-dark active:translate-y-px"
    >
      {children}
      <ArrowRight className="h-4 w-4 transition-transform duration-150 group-hover:translate-x-0.5" aria-hidden />
    </Link>
  );
}

function SecondaryLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="group inline-flex min-h-[50px] items-center gap-2 rounded-lg border border-stone-dark bg-paper px-5 text-[15px] font-semibold text-ink transition-colors hover:border-ink-soft hover:bg-butter-soft/70"
    >
      {children}
      <ArrowUpRight
        className="h-4 w-4 text-ink-soft transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
        aria-hidden
      />
    </Link>
  );
}

function TextLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="group inline-flex min-h-[44px] items-center gap-1.5 px-1 text-[14.5px] font-semibold text-ink underline decoration-stone-dark decoration-2 underline-offset-[6px] transition-colors hover:decoration-forest"
    >
      {children}
      <ArrowUpRight
        className="h-4 w-4 text-ink-soft transition-transform duration-150 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
        aria-hidden
      />
    </Link>
  );
}

function SiteNav({ cta, ctaHref, signedIn }: { cta: string; ctaHref: string; signedIn: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-3 z-40 mx-auto max-w-[1280px] px-3 pt-3 sm:px-6">
      <div className="rounded-xl border border-stone bg-paper/95 shadow-float backdrop-blur-sm">
        <div className="flex min-h-[60px] items-center gap-3 px-3.5 sm:px-5">
          <Brand />
          <nav className="ml-6 hidden items-center gap-1 lg:flex" aria-label="Sections">
            {SECTIONS.map((s) => (
              <a
                key={s.href}
                href={s.href}
                className="rounded-md px-3 py-2 text-sm font-semibold text-ink-soft transition-colors hover:bg-butter-soft/60 hover:text-ink"
              >
                {s.label}
              </a>
            ))}
          </nav>
          <div className="ml-auto hidden items-center gap-2.5 sm:flex">
            {!signedIn && (
              <Link href="/sign-in" className="btn-ghost">
                Staff &amp; doctor sign in
              </Link>
            )}
            <Link href={ctaHref} className="btn-primary">
              {cta}
              <ArrowRight className="h-4 w-4" aria-hidden />
            </Link>
          </div>
          <button
            type="button"
            className="btn-ghost ml-auto h-11 w-11 !px-0 sm:hidden"
            aria-expanded={open}
            aria-controls="mobile-menu"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="h-5 w-5" aria-hidden /> : <Menu className="h-5 w-5" aria-hidden />}
          </button>
        </div>
        {open && (
          <div id="mobile-menu" className="border-t border-stone px-3.5 pb-3.5 pt-2 sm:hidden">
            <nav className="flex flex-col" aria-label="Sections (mobile)">
              {SECTIONS.map((s) => (
                <a
                  key={s.href}
                  href={s.href}
                  onClick={() => setOpen(false)}
                  className="flex min-h-[48px] items-center border-b border-stone px-2 text-[15px] font-semibold text-ink"
                >
                  {s.label}
                </a>
              ))}
            </nav>
            <div className="mt-3 grid gap-2">
              <Link href={ctaHref} className="btn-primary">
                {cta}
                <ArrowRight className="h-4 w-4" aria-hidden />
              </Link>
              {!signedIn && (
                <Link href="/sign-in" className="btn-secondary">
                  Staff &amp; doctor sign in
                </Link>
              )}
            </div>
          </div>
        )}
      </div>
    </header>
  );
}

/** One provenance-coded layer inside the hero interface specimen. */
function Layer({
  label,
  note,
  cls,
  children,
}: {
  label: string;
  note?: string;
  cls: string;
  children: React.ReactNode;
}) {
  return (
    <div className={`bg-paper px-4 py-3 sm:px-4 ${cls}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.13em] text-ink">{label}</p>
        {note && <p className="text-[11px] text-ink-faint">{note}</p>}
      </div>
      <div className="mt-2">{children}</div>
    </div>
  );
}

/** Visual application preview showing how CareFlow separates patient evidence, model extraction, rule proposals, and human sign-off. */
function CaseSpecimen() {
  const bars = [35, 68, 52, 88, 64, 42, 78, 95, 60, 48, 82, 70, 38, 62, 75, 50, 30, 58];
  return (
    <figure className="relative" aria-label="Illustrative example of one case, with made-up information">
      {/* Warm butter architectural frame */}
      <div className="rounded-2xl border border-butter-line bg-butter/55 p-2.5 sm:p-4">
        <div className="overflow-hidden rounded-xl border border-stone-dark bg-stone shadow-float">
          {/* Application Window Header */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-stone bg-ivory px-4 py-2.5">
            <div className="flex items-center gap-2.5">
              <span className="flex gap-1.5" aria-hidden>
                <span className="h-2.5 w-2.5 rounded-full bg-stone-dark" />
                <span className="h-2.5 w-2.5 rounded-full bg-butter-line" />
                <span className="h-2.5 w-2.5 rounded-full bg-forest/60" />
              </span>
              <span className="font-mono text-xs font-semibold text-ink">CF-7A31D0E2</span>
              <span className="hidden rounded bg-paper px-2 py-0.5 font-mono text-[10px] text-ink-soft sm:inline">
                Hinglish · Voice + Lab PDF
              </span>
            </div>
            <span className="rounded border border-butter-line bg-butter-soft px-2 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.1em] text-ink">
              Synthetic · demo preview
            </span>
          </div>

          {/* 2-Column Workspace Layout inside the specimen */}
          <div className="grid gap-px lg:grid-cols-[1.2fr_.8fr]">
            {/* Left column: Patient Evidence + Model Structuring */}
            <div className="grid gap-px">
              <Layer label="Patient's words" note="Original preserved" cls="src-patient">
                {/* Mini audio waveform strip */}
                <div className="mb-2 flex items-center gap-2.5 rounded-md border border-stone bg-ivory px-2.5 py-1.5">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-forest text-white" aria-hidden>
                    <Volume2 className="h-3.5 w-3.5" />
                  </span>
                  <div className="flex flex-1 items-center gap-1 h-4" aria-hidden>
                    {bars.map((h, idx) => (
                      <span
                        key={idx}
                        className="flex-1 rounded-full bg-forest/70"
                        style={{ height: `${h}%` }}
                      />
                    ))}
                  </div>
                  <span className="font-mono text-[10.5px] text-ink-faint">0:18 · Whisper</span>
                </div>
                <p lang="hi-Latn" className="text-[14px] font-medium leading-snug text-ink">
                  “Mujhe seene mein dard hai 2 din se aur saans nahi aa rahi. Bukhar nahi hai.”
                </p>
                <div className="mt-2 rounded-md bg-slate-soft px-2.5 py-1.5 text-[12.5px] leading-snug text-ink-soft">
                  <span className="font-mono text-[9.5px] uppercase tracking-[0.1em] text-ink-faint">
                    English rendering ·{" "}
                  </span>
                  I have had chest pain for 2 days and cannot breathe. No fever.
                </div>
              </Layer>

              <Layer label="Organised by the model" note="Unverified until confirmed" cls="src-machine">
                <div className="grid grid-cols-2 gap-1.5 text-[12.5px]">
                  {[
                    { name: "Chest pain", state: "present · 2d", tone: "bg-amber-soft text-amber-clinical border-amber-line" },
                    { name: "Dyspnea", state: "present", tone: "bg-amber-soft text-amber-clinical border-amber-line" },
                    { name: "Troponin I", state: "0.18 ng/mL (PDF)", tone: "bg-crimson-soft text-crimson-clinical border-crimson-line" },
                    { name: "Fever", state: "denied", tone: "bg-moss-soft text-moss border-moss-line" },
                  ].map((f) => (
                    <div key={f.name} className="flex items-center justify-between gap-1 rounded border border-stone bg-ivory/70 px-2 py-1">
                      <span className="truncate font-medium text-ink">{f.name}</span>
                      <span className={`shrink-0 rounded border px-1.5 py-0.2 font-mono text-[10px] font-medium ${f.tone}`}>
                        {f.state}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="mt-2 flex items-center justify-between rounded bg-butter-soft/80 px-2.5 py-1 text-[11.5px] text-ink">
                  <span className="font-medium">Follow-up answered:</span>
                  <span className="text-ink-soft">Pain radiates to left arm · Age 52</span>
                </div>
              </Layer>
            </div>

            {/* Right column: Proposed Priority + Human Decision */}
            <div className="grid gap-px">
              <Layer label="Proposed priority" note="Rules engine" cls="src-proposal">
                <div className="flex items-center justify-between gap-2">
                  <QueueBadge queue="URGENT" prefix="Proposed:" />
                  <span className="rounded bg-slate-soft px-1.5 py-0.5 font-mono text-[11px] font-semibold text-ink">
                    Rank #1
                  </span>
                </div>
                <p className="mt-2 text-[12px] leading-snug text-ink-soft">
                  Matched <span className="font-mono text-[11px] text-ink">DEMO-U4</span> (chest pain + dyspnea + elevated biomarker).
                </p>
                <p className="mt-1.5 rounded border border-stone bg-ivory px-2 py-1 font-mono text-[10px] text-ink-faint">
                  Unreviewed demo ruleset
                </p>
              </Layer>

              <Layer label="Human decision" note="Staff or doctor" cls="src-human">
                <div className="space-y-1.5">
                  {[
                    { q: "Urgent", active: true, note: "Matches proposal" },
                    { q: "Needs review", active: false, note: "Override" },
                    { q: "Normal", active: false, note: "Override" },
                  ].map((opt) => (
                    <div
                      key={opt.q}
                      className={cx(
                        "flex items-center justify-between rounded-md border px-2.5 py-1.5 text-xs",
                        opt.active ? "border-forest bg-forest-soft font-semibold text-ink" : "border-stone bg-ivory/50 text-ink-soft"
                      )}
                    >
                      <span className="flex items-center gap-1.5">
                        <span
                          className={cx(
                            "h-2.5 w-2.5 rounded-full border",
                            opt.active ? "border-forest bg-forest" : "border-stone-dark bg-paper"
                          )}
                        />
                        {opt.q}
                      </span>
                      <span className="font-mono text-[10px] text-ink-faint">{opt.note}</span>
                    </div>
                  ))}
                </div>
                <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-stone pt-2 text-[11px]">
                  <span className="font-medium text-ink">Awaiting clinician sign-off</span>
                  <span className="rounded bg-forest px-2 py-0.5 font-semibold text-white">Confirm</span>
                </div>
              </Layer>
            </div>
          </div>
        </div>
      </div>
      <figcaption className="mt-2.5 flex flex-wrap items-center justify-between gap-2 px-1 text-[11.5px] text-ink-faint">
        <span> Provenance borders keep patient words, model output, rule proposals, and human decisions distinct.</span>
        <span className="font-mono text-[10.5px] uppercase tracking-[0.08em]">4-layer review model</span>
      </figcaption>
    </figure>
  );
}

/** Interactive review queue preview with working filter tabs. */
function QueueSpecimen() {
  const [filter, setFilter] = useState<"ALL" | Queue>("ALL");
  const visible = filter === "ALL" ? SAMPLE_ROWS : SAMPLE_ROWS.filter((r) => r.queue === filter);

  const tabs: { id: "ALL" | Queue; label: string; count: number }[] = [
    { id: "ALL", label: "All open", count: SAMPLE_ROWS.length },
    { id: "URGENT", label: "Urgent", count: SAMPLE_ROWS.filter((r) => r.queue === "URGENT").length },
    { id: "NEEDS_REVIEW", label: "Needs review", count: SAMPLE_ROWS.filter((r) => r.queue === "NEEDS_REVIEW").length },
    { id: "NORMAL", label: "Normal", count: SAMPLE_ROWS.filter((r) => r.queue === "NORMAL").length },
  ];

  return (
    <div className="overflow-hidden rounded-xl border border-stone bg-paper shadow-card" role="region" aria-label="Illustrative review queue with simulated data">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone bg-ivory px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Preview queue filter">
          {tabs.map((t) => {
            const active = filter === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setFilter(t.id)}
                className={cx(
                  "inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border px-3 py-1 text-xs font-semibold transition-colors",
                  active
                    ? "border-butter-line bg-butter text-ink shadow-card"
                    : "border-transparent bg-paper/70 text-ink-soft hover:border-stone hover:text-ink"
                )}
              >
                {t.label}
                <span className="rounded bg-paper/90 px-1.5 py-0.2 font-mono text-[10.5px] text-ink">
                  {t.count}
                </span>
              </button>
            );
          })}
        </div>
        <p className="rounded border border-stone-dark bg-paper px-2 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-soft">
          Simulated data — demo only
        </p>
      </div>

      <div role="table" aria-label="Simulated queue rows">
        <div
          className="hidden grid-cols-[8.5rem_minmax(0,1fr)_9.5rem_6.5rem_8.5rem] gap-x-4 border-b border-stone bg-ivory/50 px-5 py-2 font-mono text-[10.5px] uppercase tracking-[0.12em] text-ink-faint lg:grid"
          role="row"
        >
          <span role="columnheader">Reference</span>
          <span role="columnheader">Summary &amp; source</span>
          <span role="columnheader">Priority</span>
          <span role="columnheader">Waiting</span>
          <span role="columnheader">Status</span>
        </div>
        <ul className="divide-y divide-stone" role="rowgroup">
          {visible.map((r) => (
            <li
              key={r.ref}
              role="row"
              className={cx(
                "grid gap-x-4 gap-y-1.5 px-4 py-3.5 transition-colors hover:bg-butter-soft/35 sm:px-5 lg:grid-cols-[8.5rem_minmax(0,1fr)_9.5rem_6.5rem_8.5rem] lg:items-center",
                r.queue === "URGENT"
                  ? "border-l-[3px] border-l-crimson-clinical"
                  : r.queue === "NEEDS_REVIEW"
                  ? "border-l-[3px] border-l-amber-clinical"
                  : "border-l-[3px] border-l-moss"
              )}
            >
              <div role="cell" className="flex items-center gap-2">
                <span className="inline-flex h-6 min-w-[2rem] items-center justify-center rounded bg-slate-soft px-1.5 font-mono text-xs font-medium text-ink">
                  #{r.rank}
                </span>
                <span className="font-mono text-[12.5px] font-medium text-ink">{r.ref}</span>
              </div>
              <div role="cell" className="min-w-0">
                <p className="text-[15px] font-semibold leading-snug text-ink">{r.summary}</p>
                <p className="text-xs text-ink-faint">
                  {r.detail}
                  {r.note ? ` · ${r.note}` : ""}
                </p>
              </div>
              <div role="cell">
                <QueueBadge queue={r.queue} />
              </div>
              <div role="cell" className="font-mono text-xs text-ink">
                <span className="mr-1 font-sans text-xs text-ink-faint lg:hidden">Waiting</span>
                {r.wait}
              </div>
              <div role="cell" className="text-sm text-ink-soft">
                {r.status}
                <span className="block text-xs text-ink-faint">Unassigned</span>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function Welcome() {
  const { user } = useAuth();
  const workspaceHref = user ? homeFor(user.role) : "/sign-up";
  const startLabel = user ? `Open ${user.role} workspace` : "Start patient intake";
  const staffHref = user?.role === "staff" ? "/staff" : "/sign-in";
  const doctorHref = user?.role === "doctor" ? "/doctor" : "/sign-in";

  return (
    <div className="min-h-screen bg-ivory text-ink">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-paper focus:p-2"
      >
        Skip to content
      </a>
      <SiteNav cta={startLabel} ctaHref={workspaceHref} signedIn={!!user} />

      <main id="main">
        {/* ------------------------------------------------------------------ HERO */}
        <section className="relative overflow-hidden">
          {/* Warm pale-butter architectural backdrop on large screens */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-y-0 right-0 -z-0 hidden w-[46%] border-l border-butter-line/60 bg-butter-soft/75 lg:block"
          />

          <div className="relative mx-auto grid max-w-[1280px] gap-10 px-4 pb-16 pt-10 sm:px-6 sm:pt-14 lg:grid-cols-[.92fr_1.08fr] lg:items-center lg:gap-12 lg:pb-24 lg:pt-16">
            <div className="page-enter">
              <div className="inline-flex items-center gap-2 rounded-full border border-butter-line bg-butter-soft px-3.5 py-1">
                <span className="h-2 w-2 rounded-full bg-forest" aria-hidden />
                <span className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-ink">
                  Local-first clinical intake prototype
                </span>
              </div>

              <h1 className="mt-5 max-w-[13.5ch] text-[clamp(2.55rem,5.6vw,4.65rem)] font-semibold leading-[0.99] tracking-[-0.04em] text-ink">
                From a patient’s account to{" "}
                <span className="decoration-butter-line underline decoration-[6px] underline-offset-[6px]">
                  a case staff can review.
                </span>
              </h1>

              <p className="mt-5 max-w-[32rem] text-[16.5px] leading-[1.62] text-ink-soft">
                Patients share symptoms in text or voice and attach lab reports. CareFlow organises the evidence on this machine so staff and doctors can inspect the facts and record a decision.
              </p>

              <div className="mt-7 flex flex-wrap items-center gap-3">
                <PrimaryLink href={workspaceHref}>{startLabel}</PrimaryLink>
                <SecondaryLink href={staffHref}>Staff &amp; doctor sign in</SecondaryLink>
              </div>

              {/* Compact visual input-to-review strip */}
              <div className="mt-8 grid max-w-lg grid-cols-3 gap-2 rounded-xl border border-stone bg-paper p-3">
                <div className="border-r border-stone pr-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-forest">01 · Input</span>
                  <p className="mt-0.5 text-xs font-semibold text-ink">Text, Voice &amp; Reports</p>
                </div>
                <div className="border-r border-stone px-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-amber-clinical">02 · Organise</span>
                  <p className="mt-0.5 text-xs font-semibold text-ink">Facts &amp; Follow-ups</p>
                </div>
                <div className="pl-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-ink">03 · Review</span>
                  <p className="mt-0.5 text-xs font-semibold text-ink">Human Sign-Off</p>
                </div>
              </div>

              <p className="mt-5 flex max-w-[32rem] items-center gap-2 text-[12px] text-ink-faint">
                <ShieldCheck className="h-4 w-4 shrink-0 text-forest" aria-hidden />
                <span>Demonstration prototype · Does not diagnose · Not for emergencies.</span>
              </p>
            </div>

            <div className="page-enter" style={{ animationDelay: "120ms" }}>
              <CaseSpecimen />
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------------------ 3-STAGE VISUAL WORKFLOW */}
        <section id="how-it-works" className="scroll-mt-24 border-t border-stone bg-paper">
          <div className="mx-auto max-w-[1280px] px-4 py-16 sm:px-6 lg:py-24">
            <Reveal className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
              <div>
                <p className="kicker">How a case moves</p>
                <h2 className="mt-2 max-w-[18ch] text-[clamp(1.95rem,4vw,3.1rem)] font-semibold text-ink">
                  Three stages, visibly separated.
                </h2>
              </div>
              <p className="max-w-md text-[15px] leading-relaxed text-ink-soft">
                Patient evidence, machine extraction, and clinical decisions never blur together. Each stage has a clear owner.
              </p>
            </Reveal>

            <ol className="mt-10 grid gap-5 lg:grid-cols-3">
              {/* Stage 01 */}
              <Reveal as="li" delay={0} className="flex flex-col justify-between rounded-2xl border border-stone bg-ivory p-5 sm:p-6">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="rounded-md bg-butter px-2.5 py-1 font-mono text-xs font-semibold text-ink">01 · Patient</span>
                    <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-faint">Multilingual intake</span>
                  </div>
                  {/* Visual schematic */}
                  <div className="mt-4 rounded-xl border border-stone bg-paper p-3.5">
                    <div className="flex flex-wrap gap-1.5">
                      {["English", "हिन्दी", "বাংলা", "मराठी", "Hinglish"].map((l, idx) => (
                        <span
                          key={l}
                          className={cx(
                            "rounded border px-2 py-0.5 font-mono text-[10.5px]",
                            idx === 4 ? "border-forest bg-forest-soft font-semibold text-ink" : "border-stone bg-ivory text-ink-soft"
                          )}
                        >
                          {l}
                        </span>
                      ))}
                    </div>
                    <div className="mt-2.5 flex items-center justify-between rounded-lg border border-stone bg-ivory px-3 py-2 text-xs">
                      <span className="flex items-center gap-1.5 font-medium text-ink">
                        <Mic className="h-3.5 w-3.5 text-forest" aria-hidden /> Voice &amp; OCR transcript
                      </span>
                      <span className="inline-flex items-center gap-1 font-mono text-[10.5px] font-semibold text-moss">
                        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Patient checked
                      </span>
                    </div>
                  </div>
                  <h3 className="mt-5 text-xl font-semibold text-ink">Share symptoms &amp; confirm text</h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                    Type, record a voice note, or attach a lab report. Patients review and confirm every transcript before it enters the case.
                  </p>
                </div>
              </Reveal>

              {/* Stage 02 */}
              <Reveal as="li" delay={70} className="flex flex-col justify-between rounded-2xl border border-stone bg-ivory p-5 sm:p-6">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="rounded-md bg-butter px-2.5 py-1 font-mono text-xs font-semibold text-ink">02 · Local Models</span>
                    <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-faint">Evidence extraction</span>
                  </div>
                  {/* Visual schematic */}
                  <div className="mt-4 space-y-2 rounded-xl border border-stone bg-paper p-3.5 text-xs">
                    <div className="src-machine rounded bg-ivory px-2.5 py-1.5">
                      <div className="flex justify-between font-medium text-ink">
                        <span>Chest pain · present (2 days)</span>
                        <span className="font-mono text-[10px] text-ink-faint">msg #1</span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between rounded border border-amber-line bg-amber-soft/60 px-2.5 py-1.5 text-amber-clinical">
                      <span className="font-medium">Missing: Medication allergies?</span>
                      <span className="font-mono text-[10px] uppercase">Auto follow-up</span>
                    </div>
                  </div>
                  <h3 className="mt-5 text-xl font-semibold text-ink">Structure facts &amp; surface gaps</h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                    Findings link back to their exact source sentence. Missing details and contradictions trigger short follow-up questions.
                  </p>
                </div>
              </Reveal>

              {/* Stage 03 */}
              <Reveal as="li" delay={140} className="flex flex-col justify-between rounded-2xl border border-stone bg-ivory p-5 sm:p-6">
                <div>
                  <div className="flex items-center justify-between">
                    <span className="rounded-md bg-butter px-2.5 py-1 font-mono text-xs font-semibold text-ink">03 · Care Team</span>
                    <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-faint">Human accountability</span>
                  </div>
                  {/* Visual schematic */}
                  <div className="mt-4 rounded-xl border border-stone bg-paper p-3.5 text-xs">
                    <div className="flex items-center justify-between pb-2">
                      <QueueBadge queue="URGENT" prefix="Proposed:" />
                      <span className="font-mono text-[10.5px] text-ink-faint">Rule DEMO-U4</span>
                    </div>
                    <div className="flex items-center justify-between border-t border-stone pt-2">
                      <span className="font-semibold text-ink">Reviewer decision</span>
                      <span className="rounded bg-forest-soft px-2 py-0.5 font-mono text-[10.5px] font-semibold text-forest">
                        Confirm or override + reason
                      </span>
                    </div>
                  </div>
                  <h3 className="mt-5 text-xl font-semibold text-ink">Rules propose. People decide.</h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                    Staff and doctors inspect the structured summary, verify facts, and record the final priority with an audited rationale.
                  </p>
                </div>
              </Reveal>
            </ol>
          </div>
        </section>

        {/* ------------------------------------------------------------------ INTERACTIVE QUEUE PREVIEW */}
        <section id="queue" className="scroll-mt-24 border-t border-stone bg-ivory">
          <div className="mx-auto max-w-[1280px] px-4 py-16 sm:px-6 lg:py-24">
            <Reveal className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
              <div>
                <p className="kicker">The review queue</p>
                <h2 className="mt-2 max-w-[16ch] text-[clamp(1.95rem,4vw,3.1rem)] font-semibold text-ink">
                  A triage board that shows its reasoning.
                </h2>
              </div>
              <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                <TextLink href={staffHref}>Open staff queue</TextLink>
                <TextLink href={doctorHref}>Open doctor review</TextLink>
              </div>
            </Reveal>

            <Reveal className="mt-8" delay={60}>
              <QueueSpecimen />
            </Reveal>
          </div>
        </section>

        {/* ------------------------------------------------------------------ LOCAL ARCHITECTURE & SCOPE */}
        <section id="privacy" className="scroll-mt-24 border-t border-stone bg-paper">
          <div className="mx-auto max-w-[1280px] px-4 py-16 sm:px-6 lg:py-24">
            <div className="grid gap-10 lg:grid-cols-[1.05fr_.95fr] lg:items-center lg:gap-14">
              {/* Left: Visual On-Device Architecture Diagram */}
              <Reveal className="rounded-2xl border border-butter-line bg-butter-soft/55 p-5 sm:p-7">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.13em] text-ink">
                    On-device execution boundary
                  </span>
                  <span className="rounded-full border border-forest/30 bg-forest-soft px-2.5 py-0.5 font-mono text-[10px] font-semibold text-forest">
                    0 external cloud calls
                  </span>
                </div>

                <div className="mt-5 grid gap-3">
                  <div className="grid grid-cols-3 gap-2.5">
                    {[
                      { icon: Mic, title: "faster-whisper", sub: "Local speech-to-text" },
                      { icon: FileText, title: "Tesseract OCR", sub: "Local report reading" },
                      { icon: Cpu, title: "Ollama LLMs", sub: "Qwen3 & MedGemma" },
                    ].map(({ icon: Icon, title, sub }) => (
                      <div key={title} className="rounded-xl border border-stone bg-paper p-3">
                        <Icon className="h-4 w-4 text-forest" aria-hidden />
                        <p className="mt-2 font-mono text-xs font-semibold text-ink">{title}</p>
                        <p className="mt-0.5 text-[11px] leading-tight text-ink-faint">{sub}</p>
                      </div>
                    ))}
                  </div>

                  <div className="flex items-center justify-between rounded-xl border border-stone bg-paper px-4 py-3">
                    <div>
                      <p className="text-xs font-semibold text-ink">FastAPI + SQLite + Hash-Chained Audit Log</p>
                      <p className="text-[11.5px] text-ink-soft">
                        Deterministic ruleset proposes urgency · Human reviewer confirms or overrides
                      </p>
                    </div>
                    <span className="font-mono text-[11px] font-semibold text-forest">127.0.0.1</span>
                  </div>
                </div>
              </Reveal>

              {/* Right: Concise Scope & Safety Guardrails */}
              <Reveal delay={80}>
                <p className="kicker">Scope and privacy</p>
                <h2 className="mt-2 text-[clamp(1.95rem,4vw,3rem)] font-semibold text-ink">
                  Built for local clarity and human control.
                </h2>
                <div className="mt-6 divide-y divide-stone border-y border-stone">
                  {[
                    {
                      Icon: Cpu,
                      title: "Runs on this machine",
                      body: "Speech recognition, OCR, and language models execute locally without cloud fallbacks.",
                    },
                    {
                      Icon: UserCheck,
                      title: "Explicit patient consent",
                      body: "Patients accept a clear consent notice before starting an intake and can withdraw or request deletion.",
                    },
                    {
                      Icon: ScrollText,
                      title: "Audited human decisions",
                      body: "Proposals are never final. Every verification, override reason, and status change is recorded.",
                    },
                  ].map(({ Icon, title, body }) => (
                    <div key={title} className="flex items-start gap-3.5 py-4">
                      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-butter-soft text-ink">
                        <Icon className="h-4 w-4 text-forest" aria-hidden />
                      </span>
                      <div>
                        <h3 className="text-base font-semibold text-ink">{title}</h3>
                        <p className="mt-0.5 text-sm leading-relaxed text-ink-soft">{body}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </Reveal>
            </div>

            <Reveal className="mt-10 rounded-xl border border-butter-line bg-butter-soft/75 px-5 py-3.5 text-[13px] leading-relaxed text-ink">
              <span className="font-semibold">Demonstration prototype.</span> Use synthetic information only. Prioritisation rules have not been clinically reviewed, and no security or compliance certification is claimed. Not a diagnostic tool or emergency service.
            </Reveal>
          </div>
        </section>
      </main>

      <footer className="border-t border-stone bg-ivory px-4 py-6 text-center text-[12px] leading-relaxed text-ink-faint sm:px-6">
        CareFlow AI is a local demonstration prototype. Prioritisation rules are unreviewed placeholders. Use synthetic information only. Not an emergency service.
      </footer>
    </div>
  );
}
