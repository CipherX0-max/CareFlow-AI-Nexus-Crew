import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, errMessage, setCsrf } from "@/lib/api";
import { flagText, human, waiting } from "@/lib/format";

const fetchMock = vi.fn();
beforeEach(() => { vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); setCsrf(null); });
afterEach(() => { vi.unstubAllGlobals(); });
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("api client", () => {
  it("sends CSRF only on writes and always same-origin credentials", async () => {
    setCsrf("tok123");
    fetchMock.mockResolvedValue(json(200, { ok: true }));
    await api("/x");
    await api("/y", { json: { a: 1 } });
    expect(fetchMock.mock.calls[0][1].headers["X-CSRF-Token"]).toBeUndefined();
    expect(fetchMock.mock.calls[1][1].headers["X-CSRF-Token"]).toBe("tok123");
    expect(fetchMock.mock.calls[1][1].credentials).toBe("same-origin");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
  });
  it("maps the backend error shape to ApiError", async () => {
    fetchMock.mockResolvedValue(json(422, { error: { code: "weak_password", message: "Password is too easy to guess.", fields: [{ field: "password", message: "bad" }] } }));
    const e = await api("/auth/signup", { json: {} }).catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toMatchObject({ status: 422, code: "weak_password", message: "Password is too easy to guess." });
    expect(e.fields[0].field).toBe("password");
  });
  it("turns a network failure into a friendly message", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const e = await api("/x").catch((x) => x);
    expect(e.code).toBe("network_error");
    expect(errMessage(e)).toMatch(/Cannot reach the server/);
  });
  it("does not leak raw server errors", async () => {
    fetchMock.mockResolvedValue(new Response("<html>stack trace</html>", { status: 500, headers: { "content-type": "text/html" } }));
    const e = await api("/x").catch((x) => x);
    expect(e.message).not.toMatch(/stack/);
    expect(e.message).toMatch(/server had a problem/i);
  });
});

describe("formatting helpers", () => {
  it("formats waiting times", () => {
    expect(waiting(0)).toBe("just now");
    expect(waiting(59)).toBe("59 min");
    expect(waiting(125)).toBe("2 h 5 min");
    expect(waiting(60 * 60)).toBe("2 d 12 h");
    expect(waiting(null)).toBe("-");
  });
  it("humanises concepts and flags", () => {
    expect(human("other:chest_pain")).toBe("chest pain");
    expect(flagText("unit_may_be_F")).toMatch(/°F/);
    expect(flagText("custom_flag:x")).toBe("custom flag x");
  });
});
