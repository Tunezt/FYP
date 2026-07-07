"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getOwnerToken, OWNER_TOKEN_KEY } from "@/lib/api";
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

  // Owner identity for the sidebar card (fetched once; layout persists).
  const staff = useOwnerData<StaffMember[]>(ready ? "/auth/staff" : null);
  const owner = (staff.data ?? []).find((s) => s.role === "owner");

  if (!ready) return null;

  function logout() {
    localStorage.removeItem(OWNER_TOKEN_KEY);
    router.replace("/login");
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-[1400px] gap-2 md:px-4 md:py-4">
      {/* Desktop sidebar — floating panel */}
      <aside className="sticky top-4 hidden h-[calc(100vh-2rem)] w-60 shrink-0 flex-col md:flex">
        <div className="plate flex h-full flex-col px-3 py-5">
          <Link href="/overview" className="flex items-start gap-3 px-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent-gradient text-white shadow-pop">
              <IconShop className="h-6 w-6" />
            </span>
            <span>
              <span className="block text-[17px] font-bold leading-tight tracking-tight">
                Warung Pintar
              </span>
              <span className="ink-faint block text-xs leading-snug">
                Kelola usahamu, makin mudah
              </span>
            </span>
          </Link>

          <nav className="mt-7 flex flex-col gap-0.5" data-tour="nav">
            {NAV.map(({ href, label, Icon }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  className={`flex items-center gap-3 rounded-2xl px-3.5 py-2.5 text-sm transition-colors duration-150 ${
                    active
                      ? "font-semibold text-[color:var(--accent)]"
                      : "ink-soft font-medium hover:bg-[color:var(--accent-soft)]/40"
                  }`}
                  style={active ? { background: "var(--accent-soft)" } : undefined}
                >
                  <Icon className="h-5 w-5" />
                  {label}
                </Link>
              );
            })}
          </nav>

          <div
            className="mt-auto flex items-center gap-2.5 rounded-2xl px-3 py-2.5"
            style={{ border: "1px solid var(--hairline)" }}
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-gradient text-xs font-bold text-white">
              {owner ? initials(owner.name) : "…"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold leading-tight">
                {owner?.name ?? "Pemilik"}
              </span>
              <span className="ink-faint block text-[11px]">Pemilik</span>
            </span>
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
      <main className="min-w-0 flex-1 px-4 pb-28 pt-6 md:px-6 md:pb-10 md:pt-4">{children}</main>

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
