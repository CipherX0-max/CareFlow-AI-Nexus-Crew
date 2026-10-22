import { Shell } from "./Shell";

export function AuthCard({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <Shell>
      <div className="mx-auto max-w-md pt-4 sm:pt-10">
        <h1 className="text-3xl font-semibold">{title}</h1>
        {subtitle && <p className="mt-1 text-ink-soft">{subtitle}</p>}
        <div className="card mt-6 p-5 sm:p-6">{children}</div>
      </div>
    </Shell>
  );
}
