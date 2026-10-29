import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import { AgentEvent, LiveClient, LiveState } from "./live";

export interface Me {
  email: string;
  role: "customer" | "staff";
  customer_id: string | null;
  display_name: string;
}

interface Ctx {
  me: Me | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<Me>;
  logout: () => Promise<void>;
  live: LiveClient;
  liveState: LiveState;
  cartCount: number;
  refreshCart: () => void;
}

const SessionCtx = createContext<Ctx | null>(null);
export const useSession = () => {
  const c = useContext(SessionCtx);
  if (!c) throw new Error("SessionProvider missing");
  return c;
};

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [liveState, setLiveState] = useState<LiveState>("closed");
  const [cartCount, setCartCount] = useState(0);
  const live = useMemo(() => new LiveClient(getToken), []);

  const clear = useCallback(() => {
    setToken(null);
    setMe(null);
    live.stop();
  }, [live]);

  useEffect(() => {
    setUnauthorizedHandler(clear);
    const unsub = live.onState(setLiveState);
    if (getToken()) {
      api
        .get<Me>("/auth/me")
        .then(setMe)
        .catch(() => clear())
        .finally(() => setReady(true));
    } else setReady(true);
    return () => {
      unsub();
    };
  }, [clear, live]);

  useEffect(() => {
    if (me) live.start();
    else live.stop();
  }, [me, live]);

  const refreshCart = useCallback(() => {
    if (me?.role !== "customer") {
      setCartCount(0);
      return;
    }
    api.get("/cart").then((c) => setCartCount(c.item_count)).catch(() => undefined);
  }, [me]);
  useEffect(() => {
    refreshCart();
  }, [refreshCart]);

  const login = async (email: string, password: string) => {
    const r = await api.post<{ token: string; user: Me }>("/auth/login", { email, password });
    setToken(r.token);
    setMe(r.user);
    return r.user;
  };
  const logout = async () => {
    try {
      await api.post("/auth/logout");
    } catch {
      /* session may already be gone */
    }
    clear();
  };

  return <SessionCtx.Provider value={{ me, ready, login, logout, live, liveState, cartCount, refreshCart }}>{children}</SessionCtx.Provider>;
}

/** Subscribe to live agent events for the lifetime of a component. */
export function useLiveEvents(fn: (e: AgentEvent, replayed: boolean) => void, deps: unknown[] = []) {
  const { live } = useSession();
  useEffect(() => live.on(fn), [live, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
}
