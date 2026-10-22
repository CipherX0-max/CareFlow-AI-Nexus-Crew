"use client";
import { ArrowRight, ArrowUpRight, FileText, HeartPulse, Mic, ShieldCheck, Stethoscope, UserRound } from "lucide-react";
import Link from "next/link";
import { homeFor, useAuth } from "@/lib/auth";

function RouteLink({ href, children, primary = false }: { href: string; children: React.ReactNode; primary?: boolean }) {
  return <Link href={href} className={primary ? "inline-flex min-h-12 items-center gap-3 bg-[#F7F6EF] px-5 text-sm font-semibold text-[#103F35] hover:bg-white focus-visible:outline-white" : "inline-flex min-h-12 items-center gap-2 px-2 text-sm font-semibold text-[#F7F6EF] hover:text-white"}>{children}</Link>;
}

export default function Welcome() {
  const { user } = useAuth();
  const patientHref = user ? homeFor(user.role) : "/sign-up";
  const patientNavLabel = user ? `${user.role[0].toUpperCase()}${user.role.slice(1)} workspace` : "Patient intake";
  return (
    <div className="min-h-screen bg-[#F7F6EF] text-[#20342E]">
      <header className="border-b border-white/15 bg-[#103F35] text-[#F7F6EF]">
        <div className="mx-auto flex min-h-[76px] max-w-[1440px] items-center justify-between gap-4 px-5 sm:px-9 lg:px-14">
          <Link href="/" className="flex min-h-11 items-center gap-3" aria-label="CareFlow home">
            <span className="grid h-10 w-10 place-items-center rounded-full bg-[#F7F6EF] text-[#103F35]"><HeartPulse className="h-5 w-5" aria-hidden /></span>
            <span><span className="block text-xl font-semibold leading-none tracking-tight">CareFlow</span><span className="mt-1 block text-[9px] font-medium uppercase tracking-[.19em] text-[#C4D8C9]">Patient intake · Staff review</span></span>
          </Link>
          <nav className="flex items-center gap-1 sm:gap-5" aria-label="Main">
            <RouteLink href={patientHref}>{patientNavLabel} <ArrowRight className="h-4 w-4" aria-hidden /></RouteLink>
            <RouteLink href={user?.role === "staff" ? "/staff" : "/sign-in"}>Staff review <ArrowRight className="h-4 w-4" aria-hidden /></RouteLink>
            <span className="hidden items-center gap-2 pl-2 text-[9px] font-medium uppercase tracking-[.16em] text-[#D8DED3] md:inline-flex"><span className="h-1.5 w-1.5 rounded-full bg-[#C6B878]" />Local prototype</span>
          </nav>
        </div>
      </header>

      <main>
        <section className="relative overflow-hidden bg-[#103F35] text-[#F7F6EF]">
          <div className="pointer-events-none absolute -right-64 -top-72 h-[900px] w-[900px] rounded-full border border-white/[.07]" aria-hidden />
          <div className="pointer-events-none absolute -right-48 -top-52 h-[760px] w-[760px] rounded-full border border-white/[.05]" aria-hidden />
          <div className="relative mx-auto grid min-h-[610px] max-w-[1440px] items-center gap-12 px-5 py-12 sm:px-9 sm:py-16 lg:min-h-[650px] lg:grid-cols-[1.05fr_.95fr] lg:gap-14 lg:px-[6.6%] lg:py-16">
            <div className="max-w-[680px]">
              <p className="mb-7 flex items-center gap-3 text-[10px] font-semibold uppercase tracking-[.17em] text-[#D8DED3]"><span className="h-px w-7 bg-[#C6B878]" />Intake first. Staff decide.</p>
              <h1 className="max-w-[12ch] text-[2.7rem] font-semibold leading-[.99] tracking-[-.055em] sm:text-6xl lg:text-[5.1rem]">From a patient’s account to a case staff can review.</h1>
              <p className="mt-7 max-w-[600px] text-[15px] leading-7 text-[#D8E3DB] sm:text-base">
                Patients describe symptoms and can attach a report or voice note when local tools are available. The system organises submitted information and proposes a review priority. Staff inspect the evidence and decide what happens next.
              </p>
              <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-2">
                <RouteLink href={patientHref} primary>{user ? `Open ${user.role} workspace` : "Start patient intake"} <ArrowRight className="h-4 w-4" aria-hidden /></RouteLink>
                <RouteLink href={user?.role === "staff" ? "/staff" : "/sign-in"}>Staff sign in <ArrowUpRight className="h-4 w-4" aria-hidden /></RouteLink>
              </div>
              <p className="mt-7 flex items-center gap-2 text-[11px] text-[#C4D8C9]"><ShieldCheck className="h-4 w-4 shrink-0 text-[#C6B878]" aria-hidden />Local prototype · Does not diagnose · Not for emergencies</p>
            </div>

            <div className="relative mx-auto w-full max-w-[650px] bg-[#E1E8DC] px-5 py-6 text-[#20342E] sm:px-7 sm:py-7 lg:rounded-tl-[2px] lg:rounded-tr-[72px] lg:px-8 lg:py-7" aria-label="How a case reaches staff review">
              <div className="mb-5 flex items-center justify-between border-b border-[#D0D9CF] pb-4 text-[9px] font-semibold uppercase tracking-[.14em] text-[#52635A]"><span>How a case reaches review</span><span className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-[#527A64]" />Human review</span></div>
              <ol className="relative space-y-3 before:absolute before:bottom-6 before:left-[18px] before:top-6 before:w-px before:bg-[#A8B8A9]">
                <li className="relative flex min-h-[92px] items-center gap-4 border border-[#D8DED3] border-l-2 border-l-[#6F9282] bg-[#FFFEFB] px-4 py-3 sm:px-5">
                  <span className="z-10 grid h-9 w-9 shrink-0 place-items-center bg-[#E1E8DC] text-[#103F35]"><UserRound className="h-4 w-4" aria-hidden /></span>
                  <div className="min-w-0 flex-1"><p className="text-[9px] font-semibold uppercase tracking-[.13em] text-[#65756E]">01 · Patient input</p><h2 className="mt-1 text-sm font-semibold">Your account</h2><p className="mt-1 text-xs text-[#65756E]">Symptoms and optional sources</p></div>
                  <span className="hidden text-right text-[8px] font-semibold uppercase tracking-wider text-[#65756E] sm:block">Patient words</span>
                </li>
                <li className="relative ml-4 flex min-h-[92px] items-center gap-4 border border-[#D8DED3] border-l-2 border-l-[#C6B878] bg-[#F7F6EF] px-4 py-3 sm:ml-7 sm:px-5">
                  <span className="z-10 grid h-9 w-9 shrink-0 place-items-center bg-[#E1E8DC] text-[#103F35]"><FileText className="h-4 w-4" aria-hidden /></span>
                  <div className="min-w-0 flex-1"><p className="text-[9px] font-semibold uppercase tracking-[.13em] text-[#65756E]">02 · Organise</p><h2 className="mt-1 text-sm font-semibold">Structured facts</h2><p className="mt-1 text-xs text-[#65756E]">Extracted details linked to evidence</p></div>
                  <span className="hidden text-right text-[8px] font-semibold uppercase tracking-wider text-[#65756E] sm:block">Source-linked</span>
                </li>
                <li className="relative flex min-h-[92px] items-center gap-4 border border-[#D8DED3] border-l-2 border-l-[#103F35] bg-[#DCE7DC] px-4 py-3 sm:px-5">
                  <span className="z-10 grid h-9 w-9 shrink-0 place-items-center bg-[#E1E8DC] text-[#103F35]"><Stethoscope className="h-4 w-4" aria-hidden /></span>
                  <div className="min-w-0 flex-1"><p className="text-[9px] font-semibold uppercase tracking-[.13em] text-[#65756E]">03 · Staff action</p><h2 className="mt-1 text-sm font-semibold">Staff review</h2><p className="mt-1 text-xs text-[#65756E]">Priority proposal, evidence and decision</p></div>
                  <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full border border-[#9BAE9D] text-center text-[7px] font-semibold uppercase leading-tight tracking-wider text-[#52635A]">Human<br />decision</span>
                </li>
              </ol>
              <div className="mt-4 flex justify-between px-1 text-[8px] font-semibold uppercase tracking-wider text-[#65756E]"><span>Model organises</span><span>Rules propose priority</span><span>Staff decide</span></div>
              <div className="mt-5 flex items-center gap-2 border-t border-[#D0D9CF] pt-3 text-[10px] text-[#52635A]"><Mic className="h-3.5 w-3.5" aria-hidden />Voice and documents are optional and depend on local tools.</div>
            </div>
            <div className="absolute bottom-5 right-6 hidden gap-6 text-[8px] uppercase tracking-[.16em] text-[#B8CCC0] lg:flex"><span>Care / Flow</span><span>01—03</span></div>
          </div>
        </section>

        <section className="mx-auto grid max-w-[1200px] gap-8 px-5 py-10 sm:px-9 sm:py-14 lg:grid-cols-[.7fr_1.3fr] lg:px-12">
          <div><p className="text-[10px] font-semibold uppercase tracking-[.16em] text-[#6F9282]">Choose a workflow</p><h2 className="mt-3 max-w-sm text-3xl font-semibold leading-tight">Care begins with details people can check.</h2></div>
          <div className="grid gap-8 border-t border-[#D8DED3] pt-6 sm:grid-cols-2 lg:border-t-0 lg:pt-0">
            <article className="border-l-2 border-[#6F9282] pl-4"><h3 className="font-semibold">Patient intake</h3><p className="mt-2 text-sm leading-6 text-[#4C6058]">Share details in your own words. Review transcripts and extracted text before they are used in your case.</p><Link className="mt-4 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-[#103F35] underline underline-offset-4" href={user?.role === "patient" ? patientHref : "/sign-up"}>{user?.role === "patient" ? "Open your cases" : "Start an intake"} <ArrowRight className="h-4 w-4" /></Link></article>
            <article className="border-l-2 border-[#103F35] pl-4"><h3 className="font-semibold">Staff review</h3><p className="mt-2 text-sm leading-6 text-[#4C6058]">Review evidence, missing details and rule explanations in a persistent queue. Record a decision and its reason.</p><Link className="mt-4 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-[#103F35] underline underline-offset-4" href={user?.role === "staff" ? "/staff" : "/sign-in"}>Open staff sign in <ArrowRight className="h-4 w-4" /></Link></article>
          </div>
        </section>
        <footer className="border-t border-[#D8DED3] px-5 py-5 text-center text-[11px] text-[#65756E] sm:px-9">CareFlow is a demonstration prototype. Prioritisation rules have not been clinically reviewed. Use synthetic information only. Not an emergency service.</footer>
      </main>
    </div>
  );
}
