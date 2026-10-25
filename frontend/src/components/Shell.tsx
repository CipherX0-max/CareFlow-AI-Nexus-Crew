"use client";
import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { homeFor, useAuth } from "@/lib/auth";
import { cx } from "./ui";

/** CareFlow mark: an open "C" with a warm butter dot — a case arriving at a reviewer. */
export function Mark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="8" fill="#10231C" />
      <path d="M21.5 11.2A7.4 7.4 0 1 0 21.5 20.8" fill="none" stroke="#FFFDF8" strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="22.6" cy="16" r="2.3" fill="#F6E7B2" />
    </svg>
  );
}

export function Brand({ tone = "dark" }: { tone?: "dark" | "light" }) {
  return (
    <Link href="/" className="group flex min-h-[44px] items-center gap-2.5" aria-label="CareFlow AI home">
      <Mark />
      <span className={cx("font-display text-[1.22rem] font-semibold leading-none tracking-[-0.03em]", tone === "dark" ? "text-ink" : "text-white")}>
        CareFlow<span className="ml-1.5 rounded bg-butter-soft px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-ink">AI</span>
      </span>
    </Link>
  );
}

const NAV: Record<string, { href: string; label: string }[]> = {
  patient: [{ href: "/patient", label: "My cases" }],
  staff: [{ href: "/staff", label: "Intake queue" }],
  doctor: [{ href: "/doctor", label: "Clinical review" }],
  admin: [{ href: "/admin", label: "Administration" }],
};

export function Shell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const path = usePathname();
  const width = wide ? "max-w-[1400px]" : "max-w-6xl";
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-paper focus:p-2">Skip to content</a>
      <header className="sticky top-0 z-30 border-b border-stone bg-paper">
        <div className={cx("mx-auto flex items-center gap-3 px-4 sm:gap-5 sm:px-6", width)}>
          <Brand />
          <nav className="ml-2 hidden h-full gap-1 sm:flex" aria-label="Main">
            {user && NAV[user.role].map((n) => {
              const on = path.startsWith(n.href);
              return (
                <Link key={n.href} href={n.href} aria-current={on ? "page" : undefined}
                  className={cx("relative flex min-h-[56px] items-center px-3 text-sm font-semibold transition-colors", on ? "text-ink after:absolute after:inset-x-3 after:bottom-0 after:h-[2px] after:bg-cobalt" : "text-ink-soft hover:text-ink")}>{n.label}</Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            {user ? (
              <>
                <div className="hidden text-right text-xs leading-tight sm:block">
                  <div className="font-semibold text-ink">{user.display_name}</div>
                  <div className="font-mono text-[11px] capitalize text-ink-faint">{user.role}{user.is_synthetic ? " · synthetic" : ""}</div>
                </div>
                <button className="btn-secondary" onClick={async () => { await logout(); router.push("/"); }} aria-label="Sign out"><LogOut className="h-4 w-4" /><span className="hidden sm:inline">Sign out</span></button>
              </>
            ) : (
              <>
                <Link href="/sign-in" className="btn-ghost">Sign in</Link>
                <Link href="/sign-up" className="btn-primary">Create account</Link>
              </>
            )}
          </div>
        </div>
        {user && <nav className="flex gap-1 border-t border-stone px-4 sm:hidden" aria-label="Mobile main">
          {NAV[user.role].map((n) => <Link key={n.href} href={n.href} aria-current={path.startsWith(n.href) ? "page" : undefined} className={cx("inline-flex min-h-[44px] items-center border-b-2 px-2 text-sm font-semibold", path.startsWith(n.href) ? "border-cobalt text-ink" : "border-transparent text-ink-soft")}>{n.label}</Link>)}
        </nav>}
      </header>
      <main id="main" className={cx("page-enter mx-auto px-4 py-6 sm:px-6 sm:py-9", width)}>{children}</main>
      <footer className={cx("mx-auto px-4 pb-10 pt-4 text-xs leading-relaxed text-ink-faint sm:px-6", width)}>
        <div className="border-t border-stone pt-4">
          CareFlow AI is a demonstration prototype for intake and prioritisation support. It does not diagnose or give medical advice. In an emergency, call your local emergency number.
          {user && <span className="ml-1"><Link className="underline underline-offset-2 hover:text-ink" href={homeFor(user.role)}>Home</Link></span>}
        </div>
      </footer>
    </div>
  );
}
