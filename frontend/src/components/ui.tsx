"use client";
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from "react";
import type { Queue } from "@/lib/types";
import { QUEUE_LABEL } from "@/lib/format";

export const cx = (...a: (string | false | null | undefined)[]) => a.filter(Boolean).join(" ");

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("h-4 w-4 animate-spin", className)} aria-hidden />;
}
export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden />;
}

export function Button({ variant = "primary", loading, children, className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; loading?: boolean }) {
  return (
    <button {...p} disabled={p.disabled || loading} className={cx(`btn-${variant}`, className)}>
      {loading && <Spinner />}
      {children}
    </button>
  );
}

type Tone = "info" | "success" | "warn" | "danger" | "neutral";
const TONES: Record<Tone, string> = {
  info: "bg-cobalt-soft border-cobalt/25 text-cobalt-dark",
  success: "bg-moss-soft border-moss-line text-moss",
  warn: "bg-amber-soft border-amber-line text-amber-clinical",
  danger: "bg-crimson-soft border-crimson-line text-crimson-clinical",
  neutral: "bg-slate-soft border-stone text-ink-soft",
};
export function Alert({ tone = "info", title, children, className, action, role }: { tone?: Tone; title?: string; children?: React.ReactNode; className?: string; action?: React.ReactNode; role?: string }) {
  const Icon = tone === "danger" || tone === "warn" ? AlertTriangle : tone === "success" ? CheckCircle2 : Info;
  return (
    <div role={role || (tone === "danger" ? "alert" : "status")} className={cx("flex gap-3 rounded-lg border px-4 py-3 text-sm leading-snug", TONES[tone], className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">
        {title && <div className="font-semibold">{title}</div>}
        {children && <div className={title ? "mt-0.5 text-ink" : "text-ink"}>{children}</div>}
      </div>
      {action}
    </div>
  );
}

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: React.ReactNode; className?: string; title?: string }) {
  return <span title={title} className={cx("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone], className)}>{children}</span>;
}

// Priority is always shown as a text label; colour only reinforces it.
const Q_STYLE: Record<Queue, string> = {
  URGENT: "bg-crimson-clinical text-white border-crimson-clinical",
  NEEDS_REVIEW: "bg-amber-soft text-amber-clinical border-amber-line",
  NORMAL: "bg-moss-soft text-moss border-moss-line",
};
export function QueueBadge({ queue, prefix }: { queue: Queue | null | undefined; prefix?: string }) {
  if (!queue) return <Badge>Not assessed</Badge>;
  return <span className={cx("inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-[0.06em]", Q_STYLE[queue])}>{prefix ? `${prefix} ` : ""}{QUEUE_LABEL[queue]}</span>;
}

export function EmptyState({ title, children, action, icon }: { title: string; children?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-stone-dark bg-paper/60 px-6 py-14 text-center">
      {icon && <div className="mb-3 text-ink-faint">{icon}</div>}
      <h3 className="text-xl font-semibold">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-ink-soft">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label className="label" htmlFor={htmlFor}>{label}</label>
      {children}
      {hint && !error && <p className="hint">{hint}</p>}
      {error && <p className="mt-1 text-xs text-crimson-clinical" role="alert">{error}</p>}
    </div>
  );
}

export function Dialog({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) (d.showModal ? d.showModal() : d.setAttribute("open", ""));
    if (!open && d.open) d.close?.();
  }, [open]);
  if (!open) return null;
  return (
    <dialog ref={ref} aria-labelledby={id} onClose={onClose} onCancel={onClose} className={cx("rounded-xl border border-stone bg-paper p-0 shadow-float backdrop:bg-ink/50 w-[calc(100%-2rem)]", wide ? "max-w-3xl" : "max-w-lg")}>
      <div className="flex items-start justify-between border-b border-stone px-5 py-4">
        <h2 id={id} className="text-xl font-semibold">{title}</h2>
        <button onClick={onClose} aria-label="Close dialog" className="grid h-9 w-9 place-items-center rounded-md text-ink-soft transition-colors hover:bg-slate-soft hover:text-ink"><X className="h-5 w-5" /></button>
      </div>
      <div className="max-h-[70vh] overflow-y-auto px-5 py-4">{children}</div>
      {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-stone px-5 py-3">{footer}</div>}
    </dialog>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: string; count?: number; tone?: Tone }[]; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-stone">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cx("-mb-px min-h-[44px] whitespace-nowrap border-b-2 px-3.5 py-2 text-sm font-semibold transition-colors", value === t.id ? "border-cobalt text-cobalt-dark" : "border-transparent text-ink-soft hover:border-stone-dark hover:text-ink")}>
          {t.label}
          {t.count != null && <span className={cx("ml-1.5 rounded px-1.5 py-0.5 font-mono text-[11px]", t.tone === "danger" && t.count > 0 ? "bg-crimson-soft text-crimson-clinical" : "bg-slate-soft text-ink-soft")}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

// ---- toasts ----
const ToastCtx = createContext<(msg: string, tone?: Tone) => void>(() => {});
export const useToast = () => useContext(ToastCtx);
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<{ id: number; msg: string; tone: Tone }[]>([]);
  const push = useCallback((msg: string, tone: Tone = "success") => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, msg, tone }]);
    setTimeout(() => setItems((x) => x.filter((i) => i.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100%-2rem)] flex-col gap-2" aria-live="polite">
        {items.map((i) => <div key={i.id} className="pointer-events-auto"><Alert tone={i.tone}>{i.msg}</Alert></div>)}
      </div>
    </ToastCtx.Provider>
  );
}

/** Data loading with explicit loading / error / success states and a retry. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[], opts: { poll?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try { const d = await fnRef.current(); setData(d); setError(null); setStale(false); }
    catch (e: any) { setError(e); setStale(true); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    if (!opts.poll) return;
    const t = setInterval(() => { if (!document.hidden) load(true); }, opts.poll);
    return () => clearInterval(t);
  }, [opts.poll, load]);
  return { data, error, loading, reload: () => load(), reloadSilent: () => load(true), stale, setData };
}
