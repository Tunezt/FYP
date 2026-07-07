"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getOwnerToken } from "@/lib/api";
import { IconShop } from "@/components/icons";

/** Root: bounce to the dashboard if an owner session exists, else to login. */
export default function Home() {
  const router = useRouter();

  useEffect(() => {
    router.replace(getOwnerToken() ? "/overview" : "/login");
  }, [router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div className="glass-card animate-scale-in px-10 py-8 text-center">
        <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-accent-gradient text-white shadow-pop">
          <IconShop className="h-6 w-6" />
        </span>
        <p className="text-lg font-semibold">Warung Pintar</p>
        <p className="ink-soft mt-1 text-sm">Menyiapkan dashboard…</p>
      </div>
    </main>
  );
}
