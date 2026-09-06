"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getOwnerToken, OWNER_TOKEN_KEY } from "@/lib/api";
import { disableDemo, isDemo } from "@/lib/demo";
import { useOwnerData } from "@/lib/hooks";
import { initials } from "@/lib/format";
import type { StaffMember } from "@/lib/types";
import {
  IconBell,
  IconBox,
  IconChart,
  IconGear,
  IconHome,
  IconLogout,
  IconShop,
  IconUsers,
  IconWallet,
} from "@/components/icons";
import { ThemeToggle } from "@/components/ThemeToggle";

const NAV = [
  { href: "/overview", label: "Ringkasan", Icon: IconHome },
  { href: "/sales", label: "Penjualan", Icon: IconChart },
  { href: "/inventory", label: "Stok", Icon: IconBox },
  { href: "/money", label: "Keuangan", Icon: IconWallet },
  { href: "/customers", label: "Pelanggan", Icon: IconUsers },
  { href: "/alerts", label: "Peringatan", Icon: IconBell },
  { href: "/settings", label: "Pengaturan", Icon: IconGear },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    if (!getOwnerToken() && !isDemo()) {
      router.replace("/login");
    } else {
      setDemo(isDemo());
      setReady(true);
    }
  }, [router]);

  // Owner identity for the sidebar card (fetched once; layout persists).
  const staff = useOwnerData<StaffMember[]>(ready ? "/auth/staff" : null);
  const owner = (staff.data ?? []).find((s) => s.role === "owner");

  if (!ready) return null;

  function logout() {
    disableDemo();
    localStorage.removeItem(OWNER_TOKEN_KEY);
    router.replace("/login");
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-[1400px] gap-2 md:px-4 md:py-4">
      {/* Desktop sidebar — floating panel */}
      <aside className="sticky top-4 hidden h-[calc(100vh-2rem)] w-60 shrink-0 flex-col md:flex">
        <div className="plate flex h-full flex-col px-3 py-5">
          <Link href="/overview" className="flex items-center gap-3 px-3">
            <span
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-white"
              style={{ background: "var(--accent)" }}
            >
              <IconShop className="h-5 w-5" />
            </span>
            <span>
              <span className="block text-[15px] font-bold leading-tight tracking-tight">
                Warung Pintar
              </span>
              <span className="ink-faint block text-[11px] leading-snug">
                Kelola usahamu, makin mudah
              </span>
            </span>
          </Link>

          <nav className="mt-7 flex flex-col gap-1" data-tour="nav">
            {NAV.map(({ href, label, Icon }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors duration-150 ${
                    active
                      ? "font-semibold text-[color:var(--accent)]"
                      : "ink-soft font-medium hover:bg-[color:var(--accent-soft)]/40"
                  }`}
                  style={active ? { background: "var(--accent-soft)" } : undefined}
                >
                  <Icon className="h-[18px] w-[18px]" />
                  {label}
                </Link>
              );
            })}
          </nav>

          <div
            className="mt-auto flex items-center gap-2.5 rounded-xl px-2.5 py-2"
            style={{ background: "var(--accent-soft)" }}
          >
            <span
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white"
              style={{ background: "var(--accent)" }}
            >
              {owner ? initials(owner.name) : "…"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold leading-tight">
                {owner?.name ?? "Pemilik"}
              </span>
              <span className="ink-faint block text-[11px]">Pemilik</span>
            </span>
            <ThemeToggle />
            <button
              onClick={logout}
              title="Keluar"
              aria-label="Keluar"
              className="ink-faint rounded-xl p-1.5 transition-colors hover:text-[color:var(--bad)]"
            >
              <IconLogout className="h-[18px] w-[18px]" />
            </button>
          </div>
        </div>
      </aside>

      {/* Content */}
      <main className="min-w-0 flex-1 px-4 pb-28 pt-6 md:px-6 md:pb-10 md:pt-4">
        {demo && (
          <div
            className="mb-5 flex flex-wrap items-center justify-between gap-2 rounded-2xl px-4 py-2.5"
            style={{ background: "var(--warn-bg)", color: "var(--warn)" }}
          >
            <p className="text-xs font-semibold">
              Mode demo — semua angka di bawah ini data contoh, bukan data usaha asli.
            </p>
            <button
              onClick={logout}
              className="text-xs font-bold underline underline-offset-2"
            >
              Keluar mode demo
            </button>
          </div>
        )}
        {children}
      </main>

      {/* Mobile tab bar */}
      <nav className="plate fixed inset-x-3 bottom-3 z-30 flex justify-around rounded-3xl px-2 py-2 shadow-pop backdrop-blur-xl md:hidden">
        {NAV.map(({ href, label, Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex flex-col items-center gap-0.5 rounded-2xl px-2.5 py-1.5 text-[10px] font-medium ${
                active ? "text-[color:var(--accent)]" : "ink-faint"
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
