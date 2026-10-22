"use client";
import Link from "next/link";
import { useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Alert, Button, Field } from "@/components/ui";
import { api, errMessage } from "@/lib/api";

export default function Forgot() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try { setRes(await api("/auth/password/forgot", { json: { email } })); } catch (err) { setError(errMessage(err)); } finally { setBusy(false); }
  };
  return (
    <AuthCard title="Reset your password">
      {res ? (
        <div className="space-y-3">
          <Alert tone="success">{res.message}</Alert>
          {res.dev_reset_token && (
            <Alert tone="warn" title="Development mode">
              No email service is configured, so the reset link is shown here instead of being emailed.{" "}
              <Link className="font-medium underline" href={`/reset-password?token=${encodeURIComponent(res.dev_reset_token)}`}>Open reset page</Link>
            </Alert>
          )}
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Alert tone="danger">{error}</Alert>}
          <Field label="Email" htmlFor="e"><input id="e" type="email" className="input" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Button type="submit" loading={busy} className="w-full" disabled={!email}>Send reset instructions</Button>
        </form>
      )}
    </AuthCard>
  );
}
