import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../services/api";

export interface Resource<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Fetch a GET resource. ``deps`` change re-fetches; ``pollMs`` re-fetches quietly on an interval. */
export function useApi<T = any>(path: string | null, deps: unknown[] = [], pollMs?: number): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const seq = useRef(0);

  const load = useCallback(
    async (quiet = false) => {
      if (!path) return;
      const my = ++seq.current;
      if (!quiet) setLoading(true);
      try {
        const d = await api.get<T>(path);
        if (my === seq.current) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (my === seq.current) setError(e as ApiError);
      } finally {
        if (my === seq.current) setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [path, ...deps],
  );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!pollMs || !path) return;
    const t = window.setInterval(() => load(true), pollMs);
    return () => window.clearInterval(t);
  }, [load, pollMs, path]);

  return { data, error, loading, reload: () => load(true) };
}

export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(async <R,>(fn: () => Promise<R>): Promise<R | undefined> => {
    setBusy(true);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, run, setError };
}
