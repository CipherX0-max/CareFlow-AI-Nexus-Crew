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
  const [f, setF] = useState({ display_name: "", email: "", password: "", age: "", mobile: "", preferred_language: "en" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [dev, setDev] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(""); setFieldErr({});
    try {
      const r = await signup({ display_name: f.display_name, email: f.email.trim(), password: f.password, age: Number(f.age), mobile: f.mobile.trim(), preferred_language: f.preferred_language });
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
          <Field label="Age (years, required)" htmlFor="age" hint="Whole years (1-120). We do not ask for your date of birth." error={fieldErr.age}>
            <input id="age" type="number" min={1} max={120} inputMode="numeric" className="input" required value={f.age} onChange={set("age")} placeholder="e.g. 42" />
          </Field>
          <Field label="Mobile number (required)" htmlFor="mobile" hint="Shown only to authorised care-team members." error={fieldErr.mobile}>
            <input id="mobile" type="tel" inputMode="tel" className="input" required value={f.mobile} onChange={set("mobile")} placeholder="e.g. +91 98765 43210" autoComplete="tel" />
          </Field>
        </div>
        <Field label="Preferred language" htmlFor="lang">
          <select id="lang" className="input" value={f.preferred_language} onChange={set("preferred_language")}>
            <option value="en">English</option><option value="hi">हिन्दी (Hindi)</option><option value="hi-Latn">Hinglish</option>
            <option value="ta">Tamil</option><option value="te">Telugu</option><option value="bn">Bengali</option><option value="mr">Marathi</option>
          </select>
        </Field>
        <Button type="submit" loading={busy} className="w-full" disabled={!f.display_name || !f.email || !f.password || !f.age || !f.mobile.trim()}>Create account</Button>
        <p className="text-center text-sm">Already registered? <Link className="text-teal underline" href="/sign-in">Sign in</Link></p>
      </form>
    </AuthCard>
  );
}
