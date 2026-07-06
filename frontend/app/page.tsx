"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getOwnerToken } from "@/lib/api";

/** Root: bounce to the dashboard if an owner session exists, else to login. */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getOwnerToken() ? "/overview" : "/login");
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="glass-card animate-scale-in px-10 py-8 text-center">
        <div className="mx-auto mb-4 h-12 w-12 rounded-2xl bg-accent-gradient shadow-pop" />
        <p className="text-lg font-semibold">Warung Pintar</p>
        <p className="ink-soft mt-1 text-sm">Loading your workspace…</p>
      </div>
    </main>
  );
}
