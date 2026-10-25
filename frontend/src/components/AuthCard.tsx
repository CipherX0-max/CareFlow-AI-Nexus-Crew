import { ShieldCheck } from "lucide-react";
import { Shell } from "./Shell";

export function AuthCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <Shell>
      <div className="mx-auto grid max-w-5xl gap-8 pt-2 sm:pt-8 lg:grid-cols-[1fr_28rem] lg:gap-16">
        <div className="lg:pt-6">
          <p className="inline-flex items-center gap-2 rounded-full border border-butter-line bg-butter-soft px-3 py-1 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-ink">
            <span className="h-1.5 w-1.5 rounded-full bg-forest" aria-hidden />
            CareFlow AI · local prototype
          </p>
          <h1 className="page-title mt-4 !text-[clamp(2.2rem,5vw,3.6rem)] !leading-[1.02] !tracking-[-0.04em]">{title}</h1>
          {subtitle && <p className="mt-3 max-w-md text-[16px] leading-relaxed text-ink-soft">{subtitle}</p>}
          <p className="mt-8 hidden max-w-sm items-start gap-2 rounded-xl border border-stone bg-paper/80 p-3.5 text-[12.5px] leading-snug text-ink-soft lg:flex">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-forest" aria-hidden />
            <span>Local prototype. Use synthetic information only. Not a diagnostic tool or emergency service.</span>
          </p>
        </div>
        <div className="card p-5 sm:p-7">{children}</div>
      </div>
    </Shell>
  );
}
