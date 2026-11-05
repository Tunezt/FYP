"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { getOwnerToken, OWNER_TOKEN_KEY } from "@/lib/api";
import { disableDemo, isDemo } from "@/lib/demo";
import { useOwnerData } from "@/lib/hooks";
import { initials } from "@/lib/format";
import type { AlertRow, StaffMember } from "@/lib/types";
import { IconBell, IconBox, IconChart, IconChevronRight, IconGear, IconHome, IconLogout, IconMore, IconSpark, IconUsers, IconWallet } from "@/components/icons";
import { ThemeToggle } from "@/components/ThemeToggle";
import { BrandMark, Sheet } from "@/components/ui";
import { Monogram, Wordmark } from "@/components/Wordmark";
import { GlassLight, SlideIndicator, travel, travelArrived } from "@/components/motion";

/** Two groups, the way the owner uses them: what she looks at every day, and
 * what she sets up now and then. */
const NAV_DAILY = [
  { href: "/overview", label: "Ringkasan", Icon: IconHome },
  { href: "/sales", label: "Penjualan", Icon: IconChart },
  { href: "/inventory", label: "Stok", Icon: IconBox },
  { href: "/money", label: "Keuangan", Icon: IconWallet },
];
const NAV_MANAGE = [
  { href: "/customers", label: "Pelanggan", Icon: IconUsers },
  { href: "/promos", label: "Promo", Icon: IconSpark },
  { href: "/alerts", label: "Peringatan", Icon: IconBell },
  { href: "/settings", label: "Pengaturan", Icon: IconGear },
];

/** Phones get the four daily destinations as tabs; the rest live one tap away
 * under "Lainnya" — eight labels never fit a 390px bar legibly. */
const PRIMARY_MOBILE = NAV_DAILY;
const SECONDARY_MOBILE = NAV_MANAGE;

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

  // The shell is on screen: a login that travelled here can let go.
  useEffect(() => {
    if (ready) travelArrived();
  }, [ready]);

  // Close the "Lainnya" sheet once a destination is chosen.
  useEffect(() => setMoreOpen(false), [pathname]);

  // Owner identity for the sidebar (fetched once; layout persists).
  const staff = useOwnerData<StaffMember[]>(ready ? "/auth/staff" : null);
  const owner = (staff.data ?? []).find((s) => s.role === "owner");
  // The same window the Peringatan page reads, so the count beside its name
  // is the count the owner finds when she opens it. Refetched on navigation:
  // marking one "beres" there should be gone from here on the next page.
  const alerts = useOwnerData<AlertRow[]>(ready ? "/api/alerts?limit=100" : null);
  const reloadAlerts = alerts.reload;
  useEffect(() => reloadAlerts(), [pathname, reloadAlerts]);
  const openAlerts = (alerts.data ?? []).filter((a) => !a.is_acknowledged).length;

  if (!ready) return null;

  function logout() {
    disableDemo();
    localStorage.removeItem(OWNER_TOKEN_KEY);
    travel(() => router.replace("/login"));
  }

  const secondaryActive = SECONDARY_MOBILE.some(({ href }) => pathname.startsWith(href));

  const navLink = ({ href, label, Icon }: (typeof NAV_DAILY)[number]) => {
    const active = pathname.startsWith(href);
    const count = href === "/alerts" && openAlerts > 0 ? openAlerts : 0;
    return (
      <Link
        key={href}
        href={href}
        aria-current={active ? "page" : undefined}
        data-slide-active={active}
        title={label}
        className="nav-item max-xl:w-11 max-xl:justify-center max-xl:px-0"
      >
        <Icon className="nav-icon h-[19px] w-[19px] shrink-0" />
        <span className="max-xl:sr-only">{label}</span>
        {count > 0 && (
          <>
            <span className="nav-count max-xl:hidden" aria-label={`${count} belum dibaca`}>
              {count > 99 ? "99+" : count}
            </span>
            <span className="glow-dot absolute right-2 top-2 xl:hidden" aria-hidden />
          </>
        )}
      </Link>
    );
  };

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[1800px]">
      <GlassLight />
      {/* The shell: one floating ink panel holding the sign, the way around
          and the account. A full panel on a laptop or wider; a narrow rail of
          icons on a tablet, so the page keeps its room. */}
      <aside className="shell-aside sticky top-0 hidden h-screen w-[84px] shrink-0 py-4 pl-4 md:block xl:w-[276px] xl:py-5 xl:pl-5">
        <div className="shell-panel flex h-full flex-col rounded-[1.75rem] px-2.5 pb-2.5 pt-3 xl:px-3 xl:pb-3">
          {/* Brand zone. The SVG's box is exactly its artwork bounds, so the
              artwork centres on the panel; the heavy P pulls the visual mass
              left, and a 2px nudge right settles it optically. The zone's
              padding is the room the halo needs to fade without clipping. */}
          <Link
            href="/overview"
            aria-label="Poernama — ke Ringkasan"
            className="flex items-center justify-center rounded-2xl px-1 pb-5 pt-5 xl:px-4 xl:pb-7 xl:pt-8"
          >
            <BrandMark size="sm" lit className="travel-sign hidden translate-x-[2px] xl:block" />
            <Monogram className="wordmark-lit travel-sign w-[30px] xl:hidden" />
          </Link>

          <div className="shell-divider mx-3 mb-3 h-px" aria-hidden />

          <nav className="has-slide flex flex-col gap-1 max-xl:items-center" data-tour="nav" aria-label="Menu utama">
            <SlideIndicator className="nav-pill" watch={pathname} />
            {NAV_DAILY.map(navLink)}
            <div className="shell-divider mx-3 my-2 h-px self-stretch" aria-hidden />
            {NAV_MANAGE.map(navLink)}
          </nav>

          <div className="shell-well mt-auto flex items-center gap-2.5 rounded-[1.25rem] py-2.5 pl-2.5 pr-1.5 max-xl:flex-col max-xl:gap-1 max-xl:px-0 max-xl:py-2">
            <span
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
              style={{ background: "var(--shell-ink)", color: "var(--shell-bg)" }}
              title={owner?.name ?? "Pemilik"}
            >
              {owner ? initials(owner.name) : "…"}
            </span>
            <span className="min-w-0 flex-1 max-xl:hidden">
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
      <main className="min-w-0 flex-1 px-4 pb-32 pt-3 md:px-6 md:pb-12 md:pt-6 xl:px-9 xl:pt-8 2xl:px-12">
        {/* Phone: the sidebar is gone, so the sign moves to the top of the
            page. Ink on porcelain here — the lit version belongs to the
            charcoal shell. It scrolls away with the page; the tab bar stays. */}
        <div className="mb-4 flex items-center justify-between md:hidden">
          <Link href="/overview" aria-label="Poernama — ke Ringkasan" className="rounded-lg py-1.5">
            <Wordmark className="travel-sign w-[124px]" />
          </Link>
          <ThemeToggle className="h-10 w-10" />
        </div>
        {demo && (
          <div
            className="mb-6 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-full px-5 py-2.5 text-[13px] max-sm:rounded-2xl"
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
        className="float-bar has-slide !fixed inset-x-3 bottom-[max(0.75rem,env(safe-area-inset-bottom))] z-30 grid grid-cols-5 gap-0.5 rounded-[1.5rem] px-1.5 py-1.5 md:hidden"
        aria-label="Menu utama"
      >
        <SlideIndicator className="tab-pill" watch={`${pathname}:${secondaryActive}`} />
        {PRIMARY_MOBILE.map(({ href, label, Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              data-slide-active={active}
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
          data-slide-active={secondaryActive}
          className="tab-item"
        >
          <IconMore className="h-[22px] w-[22px]" />
          Lainnya
          {openAlerts > 0 && <span className="glow-dot absolute right-[calc(50%-1.1rem)] top-1.5" aria-hidden />}
        </button>
      </nav>

      <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title="Lainnya">
        <ul className="group-card group-body">
          {SECONDARY_MOBILE.map(({ href, label, Icon }) => {
            const active = pathname.startsWith(href);
            const count = href === "/alerts" ? openAlerts : 0;
            return (
              <li key={href}>
                <Link
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className="list-row list-row-action"
                  style={{ ["--row-inset" as string]: "3.5rem" }}
                >
                  <span
                    className={`flex h-9 w-9 items-center justify-center rounded-full ${
                      active ? "bg-[color:var(--accent-fill)] text-[color:var(--on-accent)]" : "surface-inset ink-soft"
                    }`}
                  >
                    <Icon className="h-[18px] w-[18px]" />
                  </span>
                  <span className={`flex-1 text-[15px] ${active ? "font-semibold" : "font-medium"}`}>{label}</span>
                  {count > 0 && <span className="glow-pill">{count > 99 ? "99+" : count}</span>}
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
