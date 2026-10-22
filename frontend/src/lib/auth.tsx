"use client";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, ApiError, setCsrf } from "./api";
import type { Me, Role, User } from "./types";

interface AuthCtx { user: User | null; me: Me | null; loading: boolean; refresh: () => Promise<void>; login: (email: string, password: string) => Promise<User>; logout: () => Promise<void>; signup: (b: any) => Promise<any> }
const Ctx = createContext<AuthCtx | null>(null);
export const homeFor = (r: Role) => (r === "patient" ? "/patient" : r === "staff" ? "/staff" : "/admin");

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      const m = await api<Me>("/auth/me");
      setCsrf(m.csrf_token);
      setMe(m);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) { setMe(null); setCsrf(null); } else if (!(e instanceof ApiError)) { setMe(null); }
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const login = useCallback(async (email: string, password: string) => {
    const r = await api<{ user: User; csrf_token: string }>("/auth/login", { json: { email, password } });
    setCsrf(r.csrf_token);
    await refresh();
    return r.user;
  }, [refresh]);
  const signup = useCallback(async (b: any) => {
    const r = await api("/auth/signup", { json: b });
    setCsrf(r.csrf_token);
    await refresh();
    return r;
  }, [refresh]);
  const logout = useCallback(async () => { try { await api("/auth/logout", { method: "POST", json: {} }); } finally { setMe(null); setCsrf(null); } }, []);
  const value = useMemo(() => ({ user: me?.user ?? null, me, loading, refresh, login, logout, signup }), [me, loading, refresh, login, logout, signup]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
export const useAuth = () => { const c = useContext(Ctx); if (!c) throw new Error("AuthProvider missing"); return c; };

/** UI convenience only. Real authorisation is enforced by the API on every request. */
export function RequireRole({ role, children }: { role: Role; children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (loading) return;
    if (!user) router.replace(`/sign-in?next=${encodeURIComponent(path)}`);
    else if (user.role !== role) router.replace(homeFor(user.role));
  }, [user, loading, role, router, path]);
  if (loading || !user || user.role !== role) return <div className="p-10"><div className="skeleton h-6 w-48" /></div>;
  return <>{children}</>;
}
