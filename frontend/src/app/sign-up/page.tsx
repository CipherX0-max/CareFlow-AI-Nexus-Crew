"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, Button, Field } from "@/components/ui";
import { ApiError, errMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function SignUp() {
  const { signup } = useAuth();
  const router = useRouter();
  const [f, setF] = useState({ display_name: "", email: "", password: "", birth_year: "", preferred_language: "en" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [dev, setDev] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(""); setFieldErr({});
    try {
      const r = await signup({ ...f, email: f.email.trim(), birth_year: f.birth_year ? Number(f.birth_year) : null });
      if (r.dev_note) { setDev(r.dev_note); setTimeout(() => router.replace("/patient"), 2500); } else router.replace("/patient");
    } catch (err) {
      setError(errMessage(err));
      if (err instanceof ApiError && err.fields) setFieldErr(Object.fromEntries(err.fields.map((x) => [x.field, x.message])));
    } finally { setBusy(false); }
  };
  return (
    <AuthCard title="Create a patient account" subtitle="Use made-up (synthetic) details while this is a demonstration.">
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error && <Alert tone="danger">{error}</Alert>}
        {dev && <Alert tone="info" title="Development mode">{dev} Continuing to your account…</Alert>}
        <Field label="Name" htmlFor="name" error={fieldErr.display_name}><input id="name" className="input" required value={f.display_name} onChange={set("display_name")} autoComplete="name" /></Field>
        <Field label="Email" htmlFor="email" error={fieldErr.email}><input id="email" type="email" className="input" required value={f.email} onChange={set("email")} autoComplete="email" /></Field>
        <Field label="Password" htmlFor="pw" hint="At least 10 characters, with letters and a number or symbol." error={fieldErr.password}><input id="pw" type="password" className="input" required value={f.password} onChange={set("password")} autoComplete="new-password" /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Year of birth (optional)" htmlFor="by" hint="Year only - we avoid collecting your full birth date." error={fieldErr.birth_year}><input id="by" inputMode="numeric" className="input" value={f.birth_year} onChange={set("birth_year")} placeholder="e.g. 1985" /></Field>
          <Field label="Preferred language" htmlFor="lang">
            <select id="lang" className="input" value={f.preferred_language} onChange={set("preferred_language")}>
              <option value="en">English</option><option value="hi">हिन्दी (Hindi)</option><option value="hi-Latn">Hinglish</option>
              <option value="ta">Tamil</option><option value="te">Telugu</option><option value="bn">Bengali</option><option value="mr">Marathi</option>
            </select>
          </Field>
        </div>
        <Button type="submit" loading={busy} className="w-full" disabled={!f.display_name || !f.email || !f.password}>Create account</Button>
        <p className="text-center text-sm">Already registered? <Link className="text-teal underline" href="/sign-in">Sign in</Link></p>
      </form>
    </AuthCard>
  );
}
