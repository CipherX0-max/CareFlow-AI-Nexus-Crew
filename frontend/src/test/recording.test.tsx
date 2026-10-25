import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Attachments } from "@/components/patient";
import { setCsrf } from "@/lib/api";

const fetchMock = vi.fn();
const stopTrack = vi.fn();
const getUserMedia = vi.fn();

class RecorderMock {
  static isTypeSupported = () => true;
  state = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onpause: (() => void) | null = null;
  onresume: (() => void) | null = null;
  start() { this.state = "recording"; }
  pause() { this.state = "paused"; this.onpause?.(); }
  resume() { this.state = "recording"; this.onresume?.(); }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob([new Uint8Array(2048)], { type: this.mimeType }) });
    this.onstop?.();
  }
}

beforeEach(() => {
  fetchMock.mockReset(); getUserMedia.mockReset(); stopTrack.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("MediaRecorder", RecorderMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:local-recording"), revokeObjectURL: vi.fn() }));
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  getUserMedia.mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] });
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ upload: { id: "u1" }, duplicate: false }), { status: 202, headers: { "content-type": "application/json" } }));
  setCsrf("recording-test");
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("browser recording intake", () => {
  it("captures locally, supports pause, previews before upload, and sends only after consent", async () => {
    const uploaded = vi.fn();
    render(<Attachments caseId="c1" config={null} onUploaded={uploaded} />);
    await userEvent.selectOptions(screen.getByLabelText("Spoken language"), "mr");
    await userEvent.click(screen.getByRole("button", { name: "Record symptoms" }));
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    await userEvent.click(screen.getByRole("button", { name: /Pause/ }));
    expect(await screen.findByRole("button", { name: /Resume/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop recording" }));
    expect(await screen.findByText("Listen before uploading")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this recording" })).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Use this recording" }));
    await waitFor(() => expect(uploaded).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/cases/c1/uploads");
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBeInstanceOf(FormData);
    expect(((fetchMock.mock.calls[0][1] as RequestInit).body as FormData).get("language")).toBe("mr");
    expect(stopTrack).toHaveBeenCalled();
  });

  it("shows an understandable microphone-denied fallback", async () => {
    getUserMedia.mockRejectedValueOnce(new Error("denied"));
    render(<Attachments caseId="c1" config={null} onUploaded={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Record symptoms" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone access was blocked");
    expect(screen.getByRole("button", { name: /Add report, photo or audio file/ })).toBeInTheDocument();
  });

  it("sends the selected language with an uploaded audio file", async () => {
    render(<Attachments caseId="c1" config={null} onUploaded={() => {}} />);
    await userEvent.selectOptions(screen.getByLabelText("Spoken language"), "bn");
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, new File([new Uint8Array(2048)], "symptoms.wav", { type: "audio/wav" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(((fetchMock.mock.calls[0][1] as RequestInit).body as FormData).get("language")).toBe("bn");
  });
});
