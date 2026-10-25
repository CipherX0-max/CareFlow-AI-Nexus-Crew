import { Check } from "lucide-react";
import { cx } from "./ui";

export type IntakeStep = { id: string; label: string; hint: string; done: boolean; optional?: boolean };

/**
 * Orientation for the draft intake. It only reflects state the page already has (messages, uploads,
 * questions, submission); it never gates or changes any action.
 */
export function IntakeProgress({ steps }: { steps: IntakeStep[] }) {
  const current = steps.findIndex((s) => !s.done && !s.optional);
  return (
    <ol className="grid gap-px overflow-hidden rounded-xl border border-stone bg-stone sm:grid-cols-4" aria-label="Intake progress">
      {steps.map((s, i) => {
        const isCurrent = i === current;
        return (
          <li key={s.id} aria-current={isCurrent ? "step" : undefined} className={cx("flex items-start gap-3 bg-paper px-4 py-3", isCurrent && "bg-butter-soft")}>
            <span className={cx("mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border text-[11px] font-semibold",
              s.done ? "border-moss bg-moss text-white" : isCurrent ? "border-ink bg-ink text-butter" : "border-stone-dark bg-paper text-ink-faint")}>
              {s.done ? <Check className="h-3.5 w-3.5" aria-hidden /> : i + 1}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-ink">{s.label}{s.optional && <span className="ml-1.5 font-mono text-[10px] font-medium uppercase tracking-[0.1em] text-ink-faint">optional</span>}</span>
              <span className="block text-xs leading-snug text-ink-faint">{s.done ? "Done" : s.hint}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
