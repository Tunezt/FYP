"use client";

import { BrandMark } from "@/components/ui";
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
      <div className="flex animate-scale-in flex-col items-center text-center">
        <BrandMark size="lg" />
        <p className="sr-only">Poernama</p>
        <p className="ink-soft mt-1 text-sm">Menyiapkan dashboard…</p>
      </div>
    </main>
  );
}
