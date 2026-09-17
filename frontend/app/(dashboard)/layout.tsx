"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getOwnerToken, OWNER_TOKEN_KEY } from "@/lib/api";
import { disableDemo, isDemo } from "@/lib/demo";
import { useOwnerData } from "@/lib/hooks";
import { initials } from "@/lib/format";
import type { StaffMember } from "@/lib/types";
import { IconBell, IconBox, IconChart, IconChevronRight, IconGear, IconHome, IconLogout, IconMore, IconSpark, IconUsers, IconWallet } from "@/components/icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { BrandMark, Sheet } from "@/components/ui";

const NAV = [
  { href: "/overview", label: "Ringkasan", Icon: IconHome },
  { href: "/sales", label: "Penjualan", Icon: IconChart },
  { href: "/inventory", label: "Stok", Icon: IconBox },
  { href: "/money", label: "Keuangan", Icon: IconWallet },
  { href: "/customers", label: "Pelanggan", Icon: IconUsers },
  { href: "/promos", label: "Promo", Icon: IconSpark },
  { href: "/alerts", label: "Peringatan", Icon: IconBell },
  { href: "/settings", label: "Pengaturan", Icon: IconGear },
];

/** Phones get the four daily destinations as tabs; the rest live one tap away
 * under "Lainnya" — eight labels never fit a 390px bar legibly. */
const PRIMARY_MOBILE = NAV.slice(0, 4);
const SECONDARY_MOBILE = NAV.slice(4);

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [demo, setDemo] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);

  useEffect(() => {
    if (!getOwnerToken() && !isDemo()) {
      router.replace("/login");
    } else {
      setDemo(isDemo());
      setReady(true);
    }
  }, [router]);

  // Close the "Lainnya" sheet once a destination is chosen.
  useEffect(() => setMoreOpen(false), [pathname]);

  // Owner identity for the sidebar (fetched once; layout persists).
  const staff = useOwnerData<StaffMember[]>(ready ? "/auth/staff" : null);
  const owner = (staff.data ?? []).find((s) => s.role === "owner");

  if (!ready) return null;

  function logout() {
    disableDemo();
    localStorage.removeItem(OWNER_TOKEN_KEY);
    router.replace("/login");
  }

  const secondaryActive = SECONDARY_MOBILE.some(({ href }) => pathname.startsWith(href));

  return (
    <div className="mx-auto flex min-h-screen max-w-[1400px]">
      {/* Desktop sidebar — the shell: one floating ink panel holding the
          wordmark, navigation and account. */}
      <aside className="sticky top-0 hidden h-screen w-[264px] shrink-0 py-4 pl-4 md:block">
        <div className="shell-panel flex h-full flex-col rounded-[1.25rem] px-3 pb-3 pt-3">
          {/* Brand zone. The SVG's box is exactly its artwork bounds, so the
              artwork centres on the panel; the heavy P pulls the visual mass
              left, and a 2px nudge right settles it optically. The zone's
              padding is the room the halo needs to fade without clipping. */}
          <Link
            href="/overview"
            aria-label="Poernama — ke Ringkasan"
            className="flex items-center justify-center rounded-2xl px-4 pb-6 pt-7"
          >
            <BrandMark size="sm" lit className="translate-x-[2px]" />
          </Link>

          <div className="shell-divider mx-3 mb-3 h-px" aria-hidden />

          <nav className="flex flex-col gap-0.5" data-tour="nav" aria-label="Menu utama">
            {NAV.map(({ href, label, Icon }) => {
              const active = pathname.startsWith(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className="nav-item"
                >
                  <Icon className="nav-icon h-[18px] w-[18px]" />
                  {label}
                </Link>
              );
            })}
          </nav>

          <div className="shell-well mt-auto flex items-center gap-2.5 rounded-2xl py-2.5 pl-2.5 pr-1.5">
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
              style={{ background: "var(--shell-ink)", color: "var(--shell-bg)" }}
            >
              {owner ? initials(owner.name) : "…"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold leading-tight">
                {owner?.name ?? "Pemilik"}
              </span>
              <span className="ink-faint block text-xs">Pemilik</span>
            </span>
            <ThemeToggle />
            <button
              onClick={logout}
              title="Keluar"
              aria-label="Keluar"
              className="icon-btn"
            >
              <IconLogout className="h-[18px] w-[18px]" />
            </button>
          </div>
        </div>
      </aside>

      {/* Content */}
      <main className="min-w-0 flex-1 px-4 pb-32 pt-5 md:px-8 md:pb-12 md:pt-4">
        {demo && (
          <div
            className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl px-4 py-2.5 text-[13px]"
            style={{ background: "var(--warn-bg)", color: "var(--warn)" }}
            role="status"
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden />
            <p className="min-w-0 flex-1 font-medium">
              Mode demo — semua angka di bawah ini data contoh, bukan data usaha asli.
            </p>
            <button onClick={logout} className="font-semibold underline decoration-1 underline-offset-[3px]">
              Keluar mode demo
            </button>
          </div>
        )}
        {children}
      </main>

      {/* Mobile tab bar — the one floating bar; content scrolls beneath it */}
      <nav
        className="float-bar fixed inset-x-3 bottom-[max(0.75rem,env(safe-area-inset-bottom))] z-30 grid grid-cols-5 rounded-3xl px-1.5 py-1.5 md:hidden"
        aria-label="Menu utama"
      >
        {PRIMARY_MOBILE.map(({ href, label, Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className="tab-item"
            >
              <Icon className="h-[22px] w-[22px]" />
              {label}
            </Link>
          );
        })}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="dialog"
          data-active={secondaryActive}
          className="tab-item"
        >
          <IconMore className="h-[22px] w-[22px]" />
          Lainnya
        </button>
      </nav>

      <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title="Lainnya">
        <ul className="group-card group-body">
          {SECONDARY_MOBILE.map(({ href, label, Icon }) => {
            const active = pathname.startsWith(href);
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className="list-row list-row-action"
                  style={{ ["--row-inset" as string]: "3.25rem" }}
                >
                  <span
                    className={`flex h-8 w-8 items-center justify-center rounded-lg ${
                      active ? "bg-[color:var(--accent-fill)] text-[color:var(--on-accent)]" : "surface-inset ink-soft"
                    }`}
                  >
                    <Icon className="h-[18px] w-[18px]" />
                  </span>
                  <span className={`flex-1 text-[15px] ${active ? "font-semibold" : "font-medium"}`}>{label}</span>
                  <IconChevronRight className="row-chevron h-4 w-4" aria-hidden />
                </Link>
              </li>
            );
          })}
        </ul>

        <div className="surface-inset mt-4 flex items-center gap-3 rounded-3xl py-2.5 pl-3 pr-2" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-[color:var(--on-accent)]"
            style={{ background: "var(--accent-fill)" }}
          >
            {owner ? initials(owner.name) : "…"}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[15px] font-semibold leading-tight">
              {owner?.name ?? "Pemilik"}
            </span>
            <span className="ink-faint block text-xs">Pemilik</span>
          </span>
          <ThemeToggle />
          <button onClick={logout} aria-label="Keluar" title="Keluar" className="icon-btn hover:text-[color:var(--bad)]">
            <IconLogout className="h-[18px] w-[18px]" />
          </button>
        </div>
      </Sheet>
    </div>
  );
}
