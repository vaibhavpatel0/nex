import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "../services/api";
import { LiveClient, LiveState } from "../services/live";
import type { Me, RuntimeEvent } from "../types";

interface Toast {
  id: number;
  text: string;
  bad?: boolean;
}

interface AppCtx {
  me: Me | null;
  ready: boolean;
  can: (perm: string) => boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  live: LiveClient;
  liveState: LiveState;
  toast: (text: string, bad?: boolean) => void;
  theme: string;
  setTheme: (t: string) => void;
}

const Ctx = createContext<AppCtx | null>(null);

function readTheme(): string {
  try {
    return localStorage.getItem("aegis.theme") || "system";
  } catch {
    return "system";
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [liveState, setLiveState] = useState<LiveState>("closed");
  const [theme, setThemeState] = useState(readTheme);
  const liveRef = useRef(new LiveClient(getToken));
  const live = liveRef.current;

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = (t: string) => {
    setThemeState(t);
    try {
      localStorage.setItem("aegis.theme", t);
    } catch {
      /* ignore */
    }
  };

  const toast = useCallback((text: string, bad = false) => {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts.slice(-3), { id, text, bad }]);
    window.setTimeout(() => setToasts((ts) => ts.filter((t) => t.id !== id)), 5000);
  }, []);

  useEffect(() => live.onState(setLiveState), [live]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null);
      live.stop();
      setMe(null);
    });
    if (!getToken()) {
      setReady(true);
      return;
    }
    api
      .get<Me>("/auth/me")
      .then((m) => {
        setMe(m);
        live.start();
      })
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, [live]);

  const value = useMemo<AppCtx>(
    () => ({
      me,
      ready,
      can: (perm) => !!me?.permissions.includes(perm),
      login: async (username, password) => {
        const r = await api.post<{ token: string; user: Me }>("/auth/login", { username, password });
        setToken(r.token);
        setMe(r.user);
        live.start();
      },
      logout: async () => {
        try {
          await api.post("/auth/logout");
        } catch {
          /* already invalid */
        }
        setToken(null);
        live.stop();
        setMe(null);
      },
      live,
      liveState,
      toast,
      theme,
      setTheme,
    }),
    [me, ready, live, liveState, toast, theme],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toast-region" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={"toast" + (t.bad ? " toast-bad" : "")}>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("AppProvider missing");
  return c;
}

/** Subscribe to live events; the callback receives each new event exactly once. */
export function useLiveEvents(fn: (e: RuntimeEvent) => void, deps: unknown[] = []) {
  const { live } = useApp();
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => live.on((e) => ref.current(e)), [live, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
}
