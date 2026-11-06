"use client";

import { BrandMark } from "@/components/ui";
import { OperatorLine } from "@/components/LegalPage";
import { MoonSpinner } from "@/components/motion";
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
    <main className="relative flex min-h-screen items-center justify-center">
      <div className="flex animate-scale-in flex-col items-center text-center">
        <BrandMark size="lg" />
        <p className="sr-only">Poernama</p>
        <p className="ink-soft mt-4 flex items-center gap-2 text-sm">
          <MoonSpinner /> Menyiapkan dashboard…
        </p>
      </div>
      {/* In the server HTML of the home page, which is what Meta's verification reads. */}
      <footer className="absolute inset-x-0 bottom-6 px-4">
        <OperatorLine />
      </footer>
    </main>
  );
}
