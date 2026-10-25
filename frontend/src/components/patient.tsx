"use client";
import { AlertOctagon, CheckCircle2, FileUp, Mic, Paperclip, Send, Square, X, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, errMessage, getCsrf } from "@/lib/api";
import { flagText, human, langLabel } from "@/lib/format";
import type { EmergencyResult, PublicConfig, Question, UploadItem, Understood } from "@/lib/types";
import { Alert, Badge, Button, Spinner, cx } from "./ui";

export function EmergencyBanner({ result, lang, config }: { result: EmergencyResult | null; lang: string; config: PublicConfig | null }) {
  if (!result?.triggered) return null;
  const m = result.message || {};
  const shown = result.language && m[result.language] ? result.language : lang; // answer in the language the patient actually wrote in
  const primary = shown === "hi" ? m.hi : shown === "hi-Latn" ? m["hi-Latn"] : m.en;
  return (
    <div role="alert" className="rounded-xl border-2 border-crimson-clinical bg-crimson-soft p-4 text-crimson-clinical shadow-card">
      <div className="flex items-start gap-3">
        <AlertOctagon className="mt-0.5 h-6 w-6 shrink-0" aria-hidden />
        <div className="space-y-2">
          <div className="text-lg font-semibold">This may need urgent help</div>
          <p className="text-ink">{primary || m.en}</p>
          {primary !== m.en && m.en && <p className="text-sm text-ink-soft">{m.en}</p>}
          {config?.emergency.number && <p className="font-semibold text-ink">Emergency number: {config.emergency.number}</p>}
          <p className="text-xs text-ink-soft">You can still finish this form, but please do not wait for it if you feel unsafe.{result.note ? ` ${result.note}` : ""}</p>
        </div>
      </div>
    </div>
  );
}

export function Composer({ caseId, disabled, maxChars, onSent, onEmergency }: { caseId: string; disabled?: boolean; maxChars: number; onSent: () => void; onEmergency: (e: EmergencyResult) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const token = useRef(crypto.randomUUID());
  useEffect(() => {
    if (text.trim().length < 3) return;
    const t = setTimeout(async () => {
      try { const r = await api<EmergencyResult>(`/cases/${caseId}/emergency-check`, { json: { text } }); if (r.triggered) onEmergency(r); } catch { /* the send path screens too */ }
    }, 700);
    return () => clearTimeout(t);
  }, [text, caseId, onEmergency]);
  const send = async () => {
    setBusy(true); setError("");
    try {
      const r = await api(`/cases/${caseId}/messages`, { json: { text, client_token: token.current } });
      if (r.emergency?.triggered) onEmergency(r.emergency);
      setText(""); token.current = crypto.randomUUID(); onSent();
    } catch (e) { setError(errMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-2">
      {error && <Alert tone="danger">{error} Your text is still here - try again.</Alert>}
      <label htmlFor="composer" className="label">Describe how you feel</label>
      <textarea id="composer" className="input min-h-[120px]" disabled={disabled || busy} value={text} maxLength={maxChars} onChange={(e) => setText(e.target.value)}
        placeholder="For example: I have had a headache for 2 days and I feel dizzy.   |   मुझे दो दिन से सिरदर्द है।   |   Mujhe 2 din se sir dard hai." />
      <div className="flex items-center justify-between gap-3">
        <span className="hint">English, Hindi or Hinglish work best. Say what you do NOT have too, if it matters. {text.length}/{maxChars}</span>
        <Button onClick={send} loading={busy} disabled={!text.trim() || disabled}><Send className="h-4 w-4" />Send</Button>
      </div>
    </div>
  );
}

async function uploadFile(caseId: string, file: Blob, name: string, language: string) {
  const fd = new FormData();
  fd.append("file", file, name);
  if (file.type.startsWith("audio/")) fd.append("language", language);
  return api<{ upload: UploadItem; duplicate: boolean }>(`/cases/${caseId}/uploads`, { form: fd });
}

export function Attachments({ caseId, config, disabled, onUploaded }: { caseId: string; config: PublicConfig | null; disabled?: boolean; onUploaded: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [language, setLanguage] = useState("auto");
  const [rec, setRec] = useState<{ mr: MediaRecorder; chunks: Blob[]; started: number; pausedAt: number | null; pausedFor: number } | null>(null);
  const [secs, setSecs] = useState(0);
  const [recording, setRecording] = useState<{ blob: Blob; name: string; url: string; seconds: number } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const maxSecs = config?.limits.max_audio_seconds ?? 300;
  const speechOff = config && !config.features.speech;

  useEffect(() => {
    if (!rec) return;
    const t = setInterval(() => { const s = Math.floor((Date.now() - rec.started - rec.pausedFor - (rec.pausedAt ? Date.now() - rec.pausedAt : 0)) / 1000); setSecs(s); if (s >= maxSecs) stop(); }, 500);
    return () => clearInterval(t); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rec]);

  useEffect(() => () => { if (recording?.url) URL.revokeObjectURL(recording.url); }, [recording?.url]);

  const doUpload = async (blob: Blob, name: string) => {
    setBusy(true); setError("");
    try { const r = await uploadFile(caseId, blob, name, language); if (r.duplicate) setError("That file was already attached."); onUploaded(); }
    catch (e) { setError(errMessage(e)); } finally { setBusy(false); }
  };
  const start = async () => {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setError("Recording is not supported in this browser. You can upload an audio file or type instead."); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m));
      const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      const started = Date.now();
      let pausedAt: number | null = null;
      let pausedFor = 0;
      mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      mr.onpause = () => { pausedAt = Date.now(); setRec((current) => current ? { ...current, pausedAt } : current); };
      mr.onresume = () => { if (pausedAt) pausedFor += Date.now() - pausedAt; pausedAt = null; setRec((current) => current ? { ...current, pausedAt: null, pausedFor } : current); };
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: mr.mimeType });
        if (blob.size > 1000) {
          const name = `recording.${mr.mimeType.includes("mp4") ? "m4a" : "webm"}`;
          const paused = pausedFor + (pausedAt ? Date.now() - pausedAt : 0);
          setRecording({ blob, name, url: URL.createObjectURL(blob), seconds: Math.max(1, Math.round((Date.now() - started - paused) / 1000)) });
        } else setError("The recording was empty. Please try again.");
      };
      mr.start();
      setRec({ mr, chunks, started, pausedAt: null, pausedFor: 0 }); setSecs(0);
    } catch { setError("Microphone access was blocked. Allow it in your browser, or upload an audio file / type instead."); }
  };
  const stop = () => { rec?.mr.state !== "inactive" && rec?.mr.stop(); setRec(null); };
  return (
    <div className="space-y-3">
      {error && <Alert tone="danger">{error}</Alert>}
      {speechOff && <Alert tone="warn">Voice transcription is not available on this system right now. Please type your symptoms - you can still attach documents.</Alert>}
      <div className="flex flex-wrap gap-2">
        {!rec ? (
          <Button variant="secondary" onClick={start} disabled={disabled || busy || !!speechOff || !!recording}><Mic className="h-4 w-4" />Record symptoms</Button>
        ) : (
          <><Button variant="secondary" onClick={() => rec.mr.state === "recording" ? rec.mr.pause() : rec.mr.resume()}><Mic className="h-4 w-4" />{rec.mr.state === "paused" ? "Resume" : "Pause"} ({secs}s / {maxSecs}s)</Button>
            <Button variant="danger" onClick={stop}><Square className="h-4 w-4" />Stop recording</Button></>
        )}
        <Button variant="secondary" onClick={() => fileRef.current?.click()} disabled={disabled || busy}><Paperclip className="h-4 w-4" />Add report, photo or audio file</Button>
        <input ref={fileRef} type="file" hidden accept="application/pdf,image/png,image/jpeg,image/webp,audio/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) doUpload(f, f.name); e.target.value = ""; }} />
        {busy && <span className="inline-flex items-center gap-2 text-sm text-ink-soft"><Spinner />Uploading…</span>}
      </div>
      <div className="max-w-sm space-y-1">
        <label htmlFor="audio-language" className="label">Spoken language</label>
        <select id="audio-language" className="input" value={language} onChange={(e) => setLanguage(e.target.value)} disabled={disabled || busy}>
          <option value="auto">Automatic detection</option><option value="en">English</option><option value="hi">Hindi</option><option value="mr">Marathi</option><option value="bn">Bengali</option><option value="hinglish">Hinglish (Hindi-English mixed)</option>
        </select>
        <p className="hint">This guides local speech recognition but does not guarantee accuracy. Hinglish uses automatic detection; please check the transcript before continuing.</p>
      </div>
      <p className="hint">Your browser will ask for microphone access. The recording stays in this page until you listen and choose to use it. If access is unavailable, upload an audio file or type instead.</p>
      {recording && <div className="w-full space-y-3 border-l-2 border-teal bg-teal-soft/50 p-4" aria-label="Recording preview">
        <div><p className="text-sm font-medium">Listen before uploading</p><p className="text-xs text-ink-soft">{recording.name} · {recording.seconds}s. The recording will be transcribed after you choose to upload it.</p></div>
        <audio className="w-full" controls src={recording.url}>Audio playback is not supported in this browser.</audio>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => { const r = recording; setRecording(null); doUpload(r.blob, r.name); }} disabled={disabled || busy}><Upload className="h-4 w-4" />Use this recording</Button>
          <Button variant="secondary" onClick={() => { setRecording(null); setSecs(0); }} disabled={busy}><Mic className="h-4 w-4" />Delete and re-record</Button>
        </div>
      </div>}
      <p className="hint">PDF, PNG/JPEG photos of reports (up to {config?.limits.max_upload_mb ?? 20} MB) or audio (up to {config?.limits.max_audio_mb ?? 25} MB / {Math.floor(maxSecs / 60)} min). Files stay on this system. You will check the extracted text before it is used.</p>
    </div>
  );
}

export function UploadCard({ caseId, up, onChanged, editable, canDelete }: { caseId: string; up: UploadItem; onChanged: () => void; editable: boolean; canDelete?: boolean }) {
  const [text, setText] = useState(up.confirmed_text ?? up.extracted_text ?? "");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [retryLanguage, setRetryLanguage] = useState(up.selected_language || "auto");
  useEffect(() => { if (!dirty) setText(up.confirmed_text ?? up.extracted_text ?? ""); }, [up.extracted_text, up.confirmed_text, dirty]);
  const kindLabel = up.kind === "audio" ? "Voice recording" : up.kind === "pdf" ? "PDF report" : "Photo of document";
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setError(""); try { await fn(); onChanged(); } catch (e) { setError(errMessage(e)); } finally { setBusy(false); } };
  const processing = up.status === "pending" || up.status === "processing";
  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <FileUp className="h-4 w-4 text-ink-soft" aria-hidden />
        <span className="font-medium">{kindLabel}</span><span className="truncate text-sm text-ink-faint">{up.filename}</span>
        <span className="ml-auto">
          {processing && <Badge tone="info"><Spinner className="h-3 w-3" />{up.kind === "audio" ? "Transcribing…" : "Reading document…"}</Badge>}
          {up.status === "needs_confirmation" && <Badge tone="warn">Please check</Badge>}
          {up.status === "confirmed" && <Badge tone="success"><CheckCircle2 className="h-3 w-3" />Confirmed</Badge>}
          {up.status === "failed" && <Badge tone="danger">Could not read</Badge>}
        </span>
      </div>
      {error && <Alert tone="danger" className="mt-3">{error}</Alert>}
      {up.status === "failed" && (
        <div className="mt-3 space-y-2">
          <Alert tone="danger">{up.error || "This file could not be processed."} It has been noted for the care team. You can retry with a clearer file, or type the key details.</Alert>
          {editable && up.kind === "audio" && <><select className="input max-w-xs" aria-label="Retry spoken language" value={retryLanguage} onChange={(e) => setRetryLanguage(e.target.value)}><option value="auto">Automatic detection</option><option value="en">English</option><option value="hi">Hindi</option><option value="mr">Marathi</option><option value="bn">Bengali</option><option value="hinglish">Hinglish</option></select><Button variant="secondary" onClick={() => act(() => api(`/cases/${caseId}/uploads/${up.id}/retry`, { json: { language: retryLanguage } }))} loading={busy}>Retry transcription</Button></>}
          {editable && <Button variant="ghost" onClick={() => act(() => api(`/cases/${caseId}/uploads/${up.id}/discard`, { json: {} }))} loading={busy}>Remove this file</Button>}
        </div>
      )}
      {(up.status === "needs_confirmation" || up.status === "confirmed") && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2 text-xs">
            {up.detected_language && <Badge tone="neutral">Spoken language: {langLabel(up.detected_language)}{up.language_confidence ? ` (${Math.round(up.language_confidence * 100)}%)` : ""}</Badge>}
            {up.selected_language && <Badge tone="neutral">Selected: {up.selected_language === "auto" ? "Automatic detection" : up.selected_language === "hinglish" ? "Hinglish" : langLabel(up.selected_language)}</Badge>}
            {up.mean_confidence != null && <Badge tone="neutral">{up.kind === "audio" ? "Transcription confidence" : "Scan confidence"}: {up.kind === "audio" ? Math.round(up.mean_confidence * 100) + "%" : Math.round(up.mean_confidence) + "%"}</Badge>}
            {up.flags.filter((f) => !f.startsWith("reviewed:")).map((f) => <Badge key={f} tone="warn">{flagText(f)}</Badge>)}
          </div>
          {up.status === "needs_confirmation" && <Alert tone="info">Please read this carefully and fix any mistakes - especially words like "no", "not" or "don't" and any numbers. Nothing is used until you confirm.</Alert>}
          {up.kind === "audio" && editable && (up.flags.includes("language_uncertain") || up.flags.includes("selected_detected_language_mismatch") || (!up.translated_text && (up.flags.includes("translation_non_english_output") || up.flags.includes("translation_unchanged_from_source") || up.flags.includes("translation_unavailable")))) && <div className="flex flex-wrap items-center gap-2"><Alert tone="warn">{(!up.translated_text && (up.flags.includes("translation_non_english_output") || up.flags.includes("translation_unchanged_from_source"))) ? "English translation failed or remained in the source language. Your original transcript is preserved below; you can retry or edit and confirm." : "Language recognition or translation needs review. Check the original transcript and retry with another language if needed."}</Alert><select className="input max-w-xs" aria-label="Retry spoken language" value={retryLanguage} onChange={(e) => setRetryLanguage(e.target.value)}><option value="auto">Automatic detection</option><option value="en">English</option><option value="hi">Hindi</option><option value="mr">Marathi</option><option value="bn">Bengali</option><option value="hinglish">Hinglish</option></select><Button variant="secondary" onClick={() => act(() => api(`/cases/${caseId}/uploads/${up.id}/retry`, { json: { language: retryLanguage, translate: true } }))} loading={busy}>Retry transcription</Button></div>}
          <label className="label" htmlFor={`t-${up.id}`}>{up.kind === "audio" ? "What we heard" : "Text we read"} {up.status === "confirmed" ? "(confirmed)" : "(you can edit)"}</label>
          <textarea id={`t-${up.id}`} className="input min-h-[140px] font-mono text-[13px]" value={text} readOnly={up.status === "confirmed" || !editable} onChange={(e) => { setText(e.target.value); setDirty(true); }} />
          {up.translated_text && <div className="rounded-lg bg-slate-soft p-3 text-sm"><div className="mb-1 text-xs font-medium text-ink-faint">English rendering (machine translation, for staff - may contain errors)</div>{up.translated_text}</div>}
          <div className="flex flex-wrap items-center gap-2">
            {up.status === "needs_confirmation" && editable && (
              <>
                <Button onClick={() => act(() => api(`/cases/${caseId}/uploads/${up.id}/confirm`, { json: { text } }))} loading={busy} disabled={!text.trim()}><CheckCircle2 className="h-4 w-4" />{dirty ? "Confirm my corrected text" : "This is correct"}</Button>
                <Button variant="ghost" onClick={() => act(() => api(`/cases/${caseId}/uploads/${up.id}`, { method: "DELETE" }))} disabled={busy}><X className="h-4 w-4" />Delete file</Button>
              </>
            )}
            {up.status === "confirmed" && canDelete && (
              <Button variant="ghost" onClick={() => act(() => api(`/cases/${caseId}/uploads/${up.id}`, { method: "DELETE" }))} disabled={busy}><X className="h-4 w-4" />Remove from draft</Button>
            )}
            <a className="btn-ghost" href={`/api/cases/${caseId}/uploads/${up.id}/file`} target="_blank" rel="noreferrer">View original</a>
          </div>
        </div>
      )}
    </div>
  );
}

export function QuestionCard({ caseId, q, onAnswered, disabled }: { caseId: string; q: Question; onAnswered: () => void; disabled?: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [why, setWhy] = useState(false);
  const submit = async (choice?: string, t?: string) => {
    setBusy(true); setError("");
    try { await api(`/cases/${caseId}/questions/${q.id}/answer`, { json: { text: t ?? text, choice: choice ?? null } }); onAnswered(); }
    catch (e) { setError(errMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="card border-teal/30 p-4">
      <p className="font-medium">{q.text}</p>
      <button className="mt-1 text-xs text-teal underline" onClick={() => setWhy(!why)} aria-expanded={why}>Why are we asking?</button>
      {why && <p className="mt-1 rounded bg-teal-soft p-2 text-xs text-ink">{q.reason}</p>}
      {error && <Alert tone="danger" className="mt-3">{error}</Alert>}
      <div className="mt-3 space-y-2">
        {q.answer_type === "choice" ? (
          <div className="flex flex-wrap gap-2">{q.options.map((o) => <Button key={o.value} variant={o.value === "unsure" ? "ghost" : "secondary"} onClick={() => submit(o.value, "")} disabled={busy || disabled}>{o.label}</Button>)}</div>
        ) : (
          <>
            <div className="flex gap-2">
              <input className="input" aria-label="Your answer" value={text} onChange={(e) => setText(e.target.value)} inputMode={q.answer_type === "number" ? "numeric" : undefined} onKeyDown={(e) => e.key === "Enter" && text.trim() && submit()} disabled={disabled}
                placeholder={q.answer_type === "number" ? "Number" : "Your answer"} />
              <Button onClick={() => submit()} loading={busy} disabled={!text.trim() || disabled}>Answer</Button>
            </div>
            <div className="flex flex-wrap gap-2">{q.options.map((o) => <Button key={o.value} variant="ghost" onClick={() => submit(o.value, "")} disabled={busy || disabled}>{o.label}</Button>)}</div>
          </>
        )}
      </div>
    </div>
  );
}

const POL: Record<string, { t: string; tone: "danger" | "success" | "warn" | "neutral" | "info" }> = {
  present: { t: "you have", tone: "warn" },
  denied: { t: "you do not have", tone: "success" },
  uncertain: { t: "not sure", tone: "neutral" },
  suspected: { t: "to be evaluated", tone: "neutral" },
  family_history_positive: { t: "family history noted", tone: "info" },
  family_history_negative: { t: "no family history", tone: "success" },
};
export function Understood({ items }: { items: Understood[] }) {
  const shown = useMemo(() => items.filter((i) => i.category !== "demographic" || i.value != null), [items]);
  if (!shown.length) return <p className="text-sm text-ink-soft">Nothing has been understood yet. Describe how you feel above and it will appear here for you to check.</p>;
  return (
    <ul className="space-y-1.5">
      {shown.map((i) => (
        <li key={i.id} className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={POL[i.polarity]?.tone ?? "neutral"}>{POL[i.polarity]?.t ?? i.polarity}</Badge>
          <span className="font-medium capitalize">{human(i.label)}{i.value != null ? `: ${i.value}${i.unit ? " " + i.unit : ""}` : ""}</span>
          {i.temporal === "resolved" && <span className="text-ink-faint">(earlier, now gone)</span>}
          {i.temporal === "past" && <span className="text-ink-faint">(in the past)</span>}
          {i.detail && <span className="text-ink-soft">- {i.detail.replace(/_/g, " ")}</span>}
        </li>
      ))}
    </ul>
  );
}

export function StatusTracker({ status }: { status: string }) {
  const steps = [["submitted", "Submitted"], ["in_review", "Being reviewed"], ["resolved", "Reviewed"]];
  const idx = status === "awaiting_patient" ? 1 : steps.findIndex(([s]) => s === status);
  return (
    <ol className="flex items-center gap-2 text-sm" aria-label="Case progress">
      {steps.map(([s, l], i) => (
        <li key={s} className="flex items-center gap-2">
          <span className={cx("grid h-6 w-6 place-items-center rounded-full text-xs font-semibold", i <= idx ? "bg-teal text-white" : "bg-stone text-ink-faint")}>{i + 1}</span>
          <span className={i <= idx ? "font-medium" : "text-ink-faint"}>{l}</span>
          {i < steps.length - 1 && <span className="h-px w-6 bg-stone-dark" />}
        </li>
      ))}
    </ol>
  );
}
