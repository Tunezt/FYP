"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, getOwnerToken, OWNER_TOKEN_KEY } from "@/lib/api";
import { demoData, isDemo } from "@/lib/demo";

/** Owner-scoped GET with auth redirect. Multiple hooks on one page fetch in
 * parallel (each fires its own request immediately on mount — no waterfall). */
export function useOwnerData<T>(path: string | null) {
  const router = useRouter();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);

  const load = useCallback(() => {
    if (!path) return;
    if (isDemo()) {
      // Short-circuit before any request: demo mode never touches the API.
      const fixture = demoData(path);
      setData(fixture as T);
      setError(fixture === null ? "Mode demo: data contoh belum tersedia di sini" : null);
      setLoading(false);
      return;
    }
    const token = getOwnerToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    const current = ++generation.current;
    setLoading(true);
    api<T>(path, { token })
      .then((result) => {
        if (generation.current === current) {
          setData(result);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (generation.current !== current) return;
        if (e instanceof ApiError && e.status === 401) {
          localStorage.removeItem(OWNER_TOKEN_KEY);
          router.replace("/login");
          return;
        }
        setError(e instanceof ApiError ? e.detail : "Gagal memuat data");
      })
      .finally(() => {
        if (generation.current === current) setLoading(false);
      });
  }, [path, router]);

  useEffect(load, [load]);

  return { data, error, loading, reload: load };
}

export function useOwnerMutation() {
  const router = useRouter();
  return useCallback(
    async <T,>(path: string, body?: unknown, method?: string): Promise<T> => {
      if (isDemo()) {
        // Accept-and-discard: the UI flow completes, fixtures stay unchanged.
        return {} as T;
      }
      const token = getOwnerToken();
      if (!token) {
        router.replace("/login");
        throw new ApiError(401, "Not logged in");
      }
      try {
        return await api<T>(path, { token, body, method: method ?? "POST" });
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          localStorage.removeItem(OWNER_TOKEN_KEY);
          router.replace("/login");
        }
        throw e;
      }
    },
    [router]
  );
}

/** True once the viewport is at least `px` wide. For the few layouts CSS
 * alone cannot express (splitting one list into two cards); everything else
 * is a breakpoint class. Starts false, so the phone layout is the first paint. */
export function useMinWidth(px: number): boolean {
  const [wide, setWide] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(`(min-width: ${px}px)`);
    const sync = () => setWide(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, [px]);
  return wide;
}
