"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getOwnerToken, OWNER_TOKEN_KEY } from "@/lib/api";
import {
  IconBell,
  IconBox,
  IconChart,
  IconGear,
  IconHome,
  IconWallet,
} from "@/components/icons";

const NAV = [
  { href: "/overview", label: "Ringkasan", Icon: IconHome },
  { href: "/sales", label: "Penjualan", Icon: IconChart },
  { href: "/inventory", label: "Stok", Icon: IconBox },
  { href: "/money", label: "Keuangan", Icon: IconWallet },
  { href: "/alerts", label: "Peringatan", Icon: IconBell },
  { href: "/settings", label: "Pengaturan", Icon: IconGear },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getOwnerToken()) {
      router.replace("/login");
    } else {
      setReady(true);
    }
  }, [router]);

  if (!ready) return null;

  function logout() {
    localStorage.removeItem(OWNER_TOKEN_KEY);
    router.replace("/login");
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col px-4 py-8 md:flex">
        <Link href="/overview" className="mb-10 flex items-center gap-3 px-2">
          <span className="h-9 w-9 rounded-xl bg-accent-gradient shadow-pop" />
          <span className="text-lg font-bold tracking-tight">Warung Pintar</span>
        </Link>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ href, label, Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 rounded-2xl px-4 py-2.5 text-sm font-medium transition-all duration-200 ${
                  active ? "glass-card glass-strong shadow-key" : "ink-soft hover:opacity-75"
                }`}
              >
                <Icon className={`h-5 w-5 ${active ? "text-accent-500" : ""}`} />
                {label}
              </Link>
            );
          })}
        </nav>
        <button onClick={logout} className="ink-faint mt-auto px-4 py-2 text-left text-sm hover:opacity-70">
          Keluar
        </button>
      </aside>

      {/* Content */}
      <main className="min-w-0 flex-1 px-4 pb-28 pt-6 md:px-8 md:pb-12 md:pt-10">{children}</main>

      {/* Mobile tab bar */}
      <nav className="glass-card glass-strong fixed inset-x-3 bottom-3 z-30 flex justify-around rounded-3xl px-2 py-2 md:hidden">
        {NAV.map(({ href, label, Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex flex-col items-center gap-0.5 rounded-2xl px-2.5 py-1.5 text-[10px] font-medium ${
                active ? "text-accent-500" : "ink-faint"
              }`}
            >
              <Icon className="h-5 w-5" />
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
