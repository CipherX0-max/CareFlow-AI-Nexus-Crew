"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, Button, Field } from "@/components/ui";
import { api, errMessage } from "@/lib/api";

function Form() {
  const token = useSearchParams().get("token") || "";
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { await api("/auth/password/reset", { json: { token, new_password: pw } }); setDone(true); } catch (err) { setError(errMessage(err)); } finally { setBusy(false); }
  };
  if (!token) return <Alert tone="danger">This reset link is missing its token. Request a new one.</Alert>;
  if (done) return <Alert tone="success">Password updated. <Link className="underline" href="/sign-in">Sign in</Link></Alert>;
  return (
    <form onSubmit={submit} className="space-y-4">
      {error && <Alert tone="danger">{error}</Alert>}
      <Field label="New password" htmlFor="pw" hint="At least 10 characters, with letters and a number or symbol."><input id="pw" type="password" className="input" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
      <Button type="submit" loading={busy} className="w-full" disabled={!pw}>Set new password</Button>
    </form>
  );
}
export default function Reset() { return <AuthCard title="Choose a new password"><Suspense><Form /></Suspense></AuthCard>; }
