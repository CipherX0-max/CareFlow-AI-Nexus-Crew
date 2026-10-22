"use client";
import { HeartPulse, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { homeFor, useAuth } from "@/lib/auth";
import { cx } from "./ui";

export function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2 font-serif text-xl font-semibold text-ink">
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-teal text-white"><HeartPulse className="h-4 w-4" aria-hidden /></span>
      CareFlow<span className="text-teal">&nbsp;AI</span>
    </Link>
  );
}

const NAV: Record<string, { href: string; label: string }[]> = {
  patient: [{ href: "/patient", label: "My cases" }],
  staff: [{ href: "/staff", label: "Queues" }],
  admin: [{ href: "/admin", label: "Administration" }],
};

export function Shell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const path = usePathname();
  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-paper focus:p-2">Skip to content</a>
      <header className="border-b border-stone bg-paper/90 backdrop-blur-0">
        <div className={cx("mx-auto flex items-center gap-4 px-4 py-3", wide ? "max-w-[1400px]" : "max-w-6xl")}>
          <Brand />
          <nav className="ml-4 hidden gap-1 sm:flex" aria-label="Main">
            {user && NAV[user.role].map((n) => (
              <Link key={n.href} href={n.href} className={cx("rounded-md px-3 py-1.5 text-sm font-medium", path.startsWith(n.href) ? "bg-teal-soft text-teal-dark" : "text-ink-soft hover:bg-stone/60")}>{n.label}</Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {user ? (
              <>
                <div className="hidden text-right text-xs leading-tight sm:block">
                  <div className="font-medium text-ink">{user.display_name}</div>
                  <div className="capitalize text-ink-faint">{user.role}{user.is_synthetic ? " · synthetic" : ""}</div>
                </div>
                <button className="btn-ghost" onClick={async () => { await logout(); router.push("/"); }} aria-label="Sign out"><LogOut className="h-4 w-4" /><span className="hidden sm:inline">Sign out</span></button>
              </>
            ) : (
              <>
                <Link href="/sign-in" className="btn-ghost">Sign in</Link>
                <Link href="/sign-up" className="btn-primary">Create account</Link>
              </>
            )}
          </div>
        </div>
        {user && <nav className="flex gap-2 border-t border-stone px-4 py-2 sm:hidden" aria-label="Mobile main">
          {NAV[user.role].map((n) => <Link key={n.href} href={n.href} aria-current={path.startsWith(n.href) ? "page" : undefined} className={cx("inline-flex min-h-10 items-center border-b-2 px-2 text-sm font-medium", path.startsWith(n.href) ? "border-teal text-teal" : "border-transparent text-ink-soft")}>{n.label}</Link>)}
        </nav>}
      </header>
      <main id="main" className={cx("mx-auto px-4 py-6 sm:py-8", wide ? "max-w-[1400px]" : "max-w-6xl")}>{children}</main>
      <footer className="mx-auto max-w-6xl px-4 pb-8 pt-4 text-xs text-ink-faint">
        CareFlow AI is a demonstration prototype for intake and prioritisation support. It does not diagnose or give medical advice. In an emergency, call your local emergency number.
        {user && <span className="ml-1"><Link className="underline" href={homeFor(user.role)}>Home</Link></span>}
      </footer>
    </div>
  );
}
