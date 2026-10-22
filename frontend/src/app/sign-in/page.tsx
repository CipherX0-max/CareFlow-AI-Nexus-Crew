"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, Button, Field } from "@/components/ui";
import { errMessage } from "@/lib/api";
import { homeFor, useAuth } from "@/lib/auth";

function Form() {
  const { login } = useAuth();
  const router = useRouter();
  const next = useSearchParams().get("next");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const u = await login(email.trim(), password);
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : homeFor(u.role));
    } catch (err) { setError(errMessage(err)); } finally { setBusy(false); }
  };
  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error && <Alert tone="danger">{error}</Alert>}
      <Field label="Email" htmlFor="email"><input id="email" className="input" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Password" htmlFor="password"><input id="password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
      <Button type="submit" loading={busy} className="w-full" disabled={!email || !password}>Sign in</Button>
      <div className="flex justify-between text-sm"><Link className="text-teal underline" href="/forgot-password">Forgot password?</Link><Link className="text-teal underline" href="/sign-up">Create patient account</Link></div>
      <p className="hint">Staff and administrator accounts are created by an administrator; they cannot be created here.</p>
    </form>
  );
}
export default function SignIn() {
  return <AuthCard title="Sign in" subtitle="Patients, care-team staff and administrators."><Suspense><Form /></Suspense></AuthCard>;
}
