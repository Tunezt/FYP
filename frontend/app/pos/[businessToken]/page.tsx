"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { POS_PAIRING_KEY } from "@/lib/api";

/** The pairing link (kasir-1). Opening it once tells this browser which café
 *  it is the till for; from then on the till lives at the short address
 *  /kasir, which is what gets bookmarked or installed on the tablet. The long
 *  link never stays in the address bar. */
export default function PairTillPage() {
  const params = useParams<{ businessToken: string }>();
  const router = useRouter();

  useEffect(() => {
    try {
      localStorage.setItem(POS_PAIRING_KEY, params.businessToken);
    } catch {}
    router.replace("/kasir");
  }, [params.businessToken, router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <p className="ink-soft animate-pulse text-lg">Menyiapkan kasir…</p>
    </main>
  );
}
