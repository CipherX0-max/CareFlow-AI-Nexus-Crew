import type { Queue } from "./types";

export const QUEUE_LABEL: Record<Queue, string> = { URGENT: "Urgent", NORMAL: "Normal", NEEDS_REVIEW: "Needs review" };

export function waiting(mins: number | null | undefined): string {
  if (mins == null) return "-";
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h} h ${mins % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}
export function when(ts: string | null | undefined): string {
  if (!ts) return "-";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "-" : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
export const human = (s: string) => s.replace(/^other:/, "").replace(/[_:]/g, " ");
export const LANG_LABEL: Record<string, string> = { en: "English", hi: "Hindi", "hi-Latn": "Hinglish (romanised Hindi)", ta: "Tamil", te: "Telugu", bn: "Bengali", mr: "Marathi", gu: "Gujarati", kn: "Kannada", ml: "Malayalam", pa: "Punjabi", or: "Odia", ur: "Urdu" };
export const langLabel = (c?: string | null) => (c ? LANG_LABEL[c] || c : "unknown");
export const FLAG_TEXT: Record<string, string> = {
  low_transcription_confidence: "Low transcription confidence - please read carefully",
  some_segments_unclear: "Some parts of the recording were unclear",
  language_uncertain: "Spoken language detected with low certainty",
  language_outside_primary_support: "Language has limited automatic support",
  mixed_scripts_possible_code_switching: "Mixed languages detected - check the text carefully",
  low_confidence_page: "Low-confidence scan",
  needs_clearer_upload: "A clearer upload is needed",
  unreadable_page: "Page could not be read",
  low_source_quality: "Derived from low-quality text",
  measurement_time_unknown: "Time of measurement not stated",
  unit_missing: "Unit not stated",
  implausible_value: "Value is not physiologically plausible as written",
  unit_may_be_F: "Unit may actually be °F",
  unit_may_be_C: "Unit may actually be °C",
};
export const flagText = (f: string) => FLAG_TEXT[f] || f.replace(/^reviewed:/, "reviewed: ").replace(/[_:]/g, " ");
