"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { IconAlert, IconCheck, IconLock, IconNote, IconPlugOff } from "@/components/icons";
import { formatQty } from "@/lib/format";
import { clockTime, minutesSince, waitLabel, type KitchenBoard, type KitchenLine, type KitchenTicket, type PrepState } from "@/lib/pos";

// Kitchen display (M11-T2, svc-4/7). Same pairing link as the till, a
// different page. Paid orders only, oldest first, in the order the work
// happens: Baru -> Disiapkan -> Siap diambil -> Diserahkan. Every move sends
// the state this screen was showing, so two screens cannot skip a step for
// each other. Polled every few seconds; a kitchen's wifi is not to be trusted
// with a socket, and a failed refresh keeps the last board on screen.

const KITCHEN_TOKEN_KEY = "wp_kitchen_token";

type StaffLite = { id: string; name: string; role: string };
type PosBusiness = { business_name: string; staff: StaffLite[] };

export default function KitchenPage() {
  const params = useParams<{ businessToken: string }>();
  const pairingToken = params.businessToken;
  const [business, setBusiness] = useState<PosBusiness | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [staff, setStaff] = useState<StaffLite | null>(null);
  const [pin, setPin] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [staffName, setStaffName] = useState("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KITCHEN_TOKEN_KEY);
      if (saved) {
        const [, payload] = saved.split(".");
        const claims = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
        if (claims.exp * 1000 > Date.now()) {
          setToken(saved);
          setStaffName(localStorage.getItem(`${KITCHEN_TOKEN_KEY}:name`) ?? "");
        }
      }
    } catch {
      /* no saved session */
    }
    api<PosBusiness>(`/pos/business/${pairingToken}`)
      .then(setBusiness)
      .catch((e: unknown) => setBootError(e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server."));
  }, [pairingToken]);

  async function login() {
    if (!staff || pin.length < 4) return;
    setLoginError(null);
    try {
      const res = await api<{ token: string; staff_name: string; business_name: string }>("/pos/login", {
        body: { pairing_token: pairingToken, staff_id: staff.id, pin },
      });
      localStorage.setItem(KITCHEN_TOKEN_KEY, res.token);
      localStorage.setItem(`${KITCHEN_TOKEN_KEY}:name`, res.staff_name);
      setToken(res.token);
      setStaffName(res.staff_name);
      setPin("");
    } catch (e: unknown) {
      setLoginError(e instanceof ApiError ? e.detail : "PIN salah — coba lagi");
      setPin("");
    }
  }

  function lock() {
    localStorage.removeItem(KITCHEN_TOKEN_KEY);
    localStorage.removeItem(`${KITCHEN_TOKEN_KEY}:name`);
    setToken(null);
    setStaff(null);
  }

  if (bootError) {
    return (
      <main className="flex min-h-screen items-center justify-center px-6 text-center">
        <div className="glass-card px-6 py-8">
          <span className="surface-inset ink-faint mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
            <IconPlugOff className="h-6 w-6" />
          </span>
          <p className="text-lg font-semibold tracking-[-0.015em]">Layar dapur tidak bisa dibuka</p>
          <p className="ink-soft mt-2 text-sm">{bootError}</p>
        </div>
      </main>
    );
  }

  if (token) {
    return <Board token={token} businessName={business?.business_name ?? ""} staffName={staffName} onLock={lock} onExpired={lock} />;
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6">
      <p className="ink-faint text-[13px] font-medium">{business?.business_name ?? "…"}</p>
      <h1 className="mt-1 text-[1.75rem] font-semibold tracking-[-0.025em]">Layar dapur</h1>
      <p className="ink-soft mt-1 text-sm">Siapa yang jaga dapur? Pilih nama, masukkan PIN kasir.</p>
      <div className="mt-5 grid grid-cols-2 gap-2">
        {(business?.staff ?? []).map((s) => (
          <button
            key={s.id}
            onClick={() => {
              setStaff(s);
              setPin("");
              setLoginError(null);
            }}
            aria-pressed={staff?.id === s.id}
            className="choice-card px-4 py-3.5 text-[15px]"
          >
            {s.name}
          </button>
        ))}
      </div>
      {staff && (
        <div className="mt-5 flex gap-2">
          <input
            autoFocus
            type="password"
            inputMode="numeric"
            value={pin}
            onChange={(e) => setPin(e.target.value.replace(/[^0-9]/g, "").slice(0, 6))}
            onKeyDown={(e) => e.key === "Enter" && login()}
            aria-label="PIN kasir"
            className="field flex-1 py-3 text-center text-2xl font-semibold tracking-[0.5em] placeholder:tracking-normal"
            placeholder="PIN"
          />
          <button onClick={login} disabled={pin.length < 4} className="btn-accent px-6 py-3">
            Masuk
          </button>
        </div>
      )}
      {loginError && <p className="notice notice-bad mt-4">{loginError}</p>}
    </main>
  );
}

function Board({
  token,
  businessName,
  staffName,
  onLock,
  onExpired,
}: {
  token: string;
  businessName: string;
  staffName: string;
  onLock: () => void;
  onExpired: () => void;
}) {
  const [board, setBoard] = useState<KitchenBoard | null>(null);
  const [tab, setTab] = useState<"queue" | "history">("queue");
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const [notices, setNotices] = useState<Record<string, string>>({});
  // A refused move can take its card off the board (another screen handed it
  // over), so the explanation also lives above the columns for a while.
  const [banner, setBanner] = useState<string | null>(null);
  const bannerTimer = useRef<number | undefined>(undefined);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [lastOk, setLastOk] = useState<number | null>(null);
  const [offset, setOffset] = useState(0); // server clock − this tablet's clock
  const [, setTick] = useState(0);
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const data = await api<KitchenBoard>("/pos/kitchen/board", { token });
      setBoard(data);
      setOffset(new Date(data.server_time).getTime() - Date.now());
      setLastOk(Date.now());
      setFetchError(null);
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      // Keep the last board on screen: a failed refresh must never look like an empty kitchen.
      setFetchError(e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server");
    } finally {
      inFlight.current = false;
    }
  }, [token, onExpired]);

  useEffect(() => {
    void load();
    const poll = setInterval(() => void load(), 4000);
    const clock = setInterval(() => setTick((n) => n + 1), 15000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [load]);

  const now = Date.now() + offset;
  const staleSeconds = lastOk ? Math.floor((Date.now() - lastOk) / 1000) : null;
  const stale = fetchError !== null || (staleSeconds !== null && staleSeconds > 20);

  function notify(id: string, message: string | null) {
    setNotices((n) => {
      const next = { ...n };
      if (message) next[id] = message;
      else delete next[id];
      return next;
    });
    if (message)
      window.setTimeout(
        () =>
          setNotices((n) => {
            if (n[id] !== message) return n;
            const next = { ...n };
            delete next[id];
            return next;
          }),
        8000
      );
  }

  function announce(message: string) {
    setBanner(message);
    window.clearTimeout(bannerTimer.current);
    bannerTimer.current = window.setTimeout(() => setBanner(null), 8000);
  }

  function replaceTicket(updated: KitchenTicket) {
    setBoard((b) => {
      if (!b) return b;
      const tickets =
        updated.state === "done" ? b.tickets.filter((t) => t.order_id !== updated.order_id) : b.tickets.map((t) => (t.order_id === updated.order_id ? updated : t));
      return { ...b, tickets, cancellations: b.cancellations.filter((t) => t.order_id !== updated.order_id) };
    });
  }

  /** One tap, one move. The state the tablet was showing goes with it, so a
   *  second tablet that already moved the ticket wins and this one is told. */
  async function move(t: KitchenTicket, state: PrepState) {
    if (busy[t.order_id]) return;
    setBusy((b) => ({ ...b, [t.order_id]: true }));
    notify(t.order_id, null);
    try {
      const updated = await api<KitchenTicket>(`/pos/kitchen/${t.order_id}/state`, {
        token,
        body: t.status === "completed" ? { state, expected: t.state } : { state },
      });
      replaceTicket(updated);
    } catch (e: unknown) {
      const message = e instanceof ApiError ? e.detail : "Belum tersimpan — periksa koneksi, lalu coba lagi.";
      notify(t.order_id, message);
      announce(`${t.code}: ${message}`);
      void load();
    } finally {
      setBusy((b) => ({ ...b, [t.order_id]: false }));
    }
  }

  async function toggleLine(t: KitchenTicket, line: KitchenLine) {
    if (!line.line_id || busy[t.order_id] || t.state === "ready") return;
    setBusy((b) => ({ ...b, [t.order_id]: true }));
    notify(t.order_id, null);
    try {
      const updated = await api<KitchenTicket>(`/pos/kitchen/${t.order_id}/lines/${line.line_id}`, { token, body: { done: !line.done } });
      replaceTicket(updated);
    } catch (e: unknown) {
      const message = e instanceof ApiError ? e.detail : "Belum tersimpan — coba lagi.";
      notify(t.order_id, message);
      announce(`${t.code}: ${message}`);
      void load();
    } finally {
      setBusy((b) => ({ ...b, [t.order_id]: false }));
    }
  }

  const columns: { state: PrepState; title: string; hint: string }[] = [
    { state: "new", title: "Baru", hint: "Sudah dibayar, belum dimulai" },
    { state: "preparing", title: "Disiapkan", hint: "Centang item yang sudah jadi" },
    { state: "ready", title: "Siap diambil", hint: "Tunggu sampai diserahkan" },
  ];
  const tickets = board?.tickets ?? [];

  return (
    <main className="min-h-[100dvh]">
      <header className="hairline-b sticky-bar sticky top-0 z-20">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-5 gap-y-2 px-4 py-2.5 sm:px-6">
          <div className="min-w-0">
            <p className="ink-faint truncate text-[12px] font-medium">{businessName}</p>
            <p className="truncate text-[17px] font-semibold tracking-[-0.015em]">Dapur · {staffName}</p>
          </div>
          <div className="segmented" role="tablist" aria-label="Tampilan dapur">
            <button role="tab" aria-selected={tab === "queue"} onClick={() => setTab("queue")} className="segmented-item min-h-[2.75rem] px-4">
              Antrean <span className="tabular-nums">{tickets.length}</span>
            </button>
            <button role="tab" aria-selected={tab === "history"} onClick={() => setTab("history")} className="segmented-item min-h-[2.75rem] px-4">
              Sudah diserahkan
            </button>
          </div>
          <div className="ml-auto flex items-center gap-3">
            <p className="flex items-center gap-2 text-[13px] font-medium" role="status" style={{ color: stale ? "var(--warn)" : "var(--ink-faint)" }}>
              <span className="h-2 w-2 rounded-full" style={{ background: stale ? "var(--warn)" : "var(--good)" }} />
              {board === null && !fetchError
                ? "Menghubungkan…"
                : stale
                  ? `Koneksi terputus${lastOk ? ` · data dari ${clockTime(new Date(lastOk + offset).toISOString())}` : ""}`
                  : "Tersambung"}
            </p>
            {stale && (
              <button onClick={() => void load()} className="btn-quiet px-3 py-2 text-sm">
                Coba lagi
              </button>
            )}
            <button onClick={onLock} className="icon-btn ink-soft h-11 w-auto gap-1.5 rounded-xl px-3 text-sm" title="Kunci layar dapur">
              <IconLock className="h-[18px] w-[18px]" /> Kunci
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] px-4 pb-10 pt-4 sm:px-6">
        {board === null ? (
          fetchError ? (
            <div className="glass-card mx-auto mt-10 max-w-md px-6 py-10 text-center">
              <span className="surface-inset ink-faint mx-auto flex h-12 w-12 items-center justify-center rounded-2xl">
                <IconPlugOff className="h-6 w-6" />
              </span>
              <p className="mt-4 text-lg font-semibold">Antrean belum bisa dimuat</p>
              <p className="ink-soft mt-1 text-sm">{fetchError}. Pesanan tidak hilang — layar akan mencoba lagi otomatis.</p>
              <button onClick={() => void load()} className="btn-accent mt-5 px-6 py-3">
                Coba sekarang
              </button>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="glass-card h-64 animate-pulse" />
              ))}
            </div>
          )
        ) : tab === "history" ? (
          <History tickets={board.history} now={now} />
        ) : (
          <>
            {banner && (
              <p role="alert" className="notice notice-warn mb-4 flex items-center justify-between gap-3 text-[15px]">
                {banner}
                <button onClick={() => setBanner(null)} className="rounded-lg px-2 py-1 text-sm underline underline-offset-2">
                  Tutup
                </button>
              </p>
            )}
            {board.cancellations.length > 0 && (
              <section aria-label="Pesanan dibatalkan" className="mb-4 space-y-2">
                {board.cancellations.map((t) => (
                  <div key={t.order_id} role="alert" className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-2xl px-4 py-3" style={{ background: "var(--bad-bg)", color: "var(--bad)" }}>
                    <IconAlert className="h-5 w-5 shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-[17px] font-semibold">
                        {t.code} {t.status === "refunded" ? "dikembalikan" : "dibatalkan"} — hentikan pembuatan
                      </p>
                      <p className="text-sm">
                        {lineText(t.lines)}
                        {t.reversed_by ? ` · disetujui ${t.reversed_by}` : ""}
                        {t.reversal_reason ? ` · alasan: ${t.reversal_reason}` : ""}
                      </p>
                      {notices[t.order_id] && <p className="text-sm font-medium">{notices[t.order_id]}</p>}
                    </div>
                    <button onClick={() => move(t, "done")} disabled={busy[t.order_id]} className="btn-quiet min-h-[2.75rem] px-4 text-sm" style={{ color: "var(--bad)" }}>
                      Oke, mengerti
                    </button>
                  </div>
                ))}
              </section>
            )}

            {tickets.length === 0 && board.cancellations.length === 0 ? (
              <div className="mx-auto mt-16 max-w-md text-center">
                <p className="text-[22px] font-semibold tracking-[-0.02em]">Antrean kosong</p>
                <p className="ink-soft mt-1">Pesanan yang sudah dibayar muncul di sini otomatis, yang terlama di atas.</p>
                {stale && <p className="mt-3 text-sm font-medium" style={{ color: "var(--warn)" }}>Koneksi sedang terputus — bisa jadi ada pesanan yang belum tampil.</p>}
              </div>
            ) : (
              <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
                {columns.map((col) => {
                  const inCol = tickets.filter((t) => t.state === col.state);
                  return (
                    <section key={col.state} aria-label={col.title} className="min-w-0">
                      <div className="mb-2.5 flex items-baseline justify-between px-1">
                        <h2 className="text-[17px] font-semibold">
                          {col.title} <span className="ink-faint font-normal tabular-nums">{inCol.length}</span>
                        </h2>
                        <p className="ink-faint hidden text-xs lg:block">{col.hint}</p>
                      </div>
                      {inCol.length === 0 ? (
                        <p className="ink-faint rounded-2xl px-4 py-6 text-center text-sm" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
                          Tidak ada
                        </p>
                      ) : (
                        <ul className="space-y-3">
                          {inCol.map((t) => (
                            <li key={t.order_id}>
                              <TicketCard ticket={t} now={now} busy={!!busy[t.order_id]} notice={notices[t.order_id]} onMove={move} onToggle={toggleLine} />
                            </li>
                          ))}
                        </ul>
                      )}
                    </section>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}

const SERVICE: Record<string, string> = { dine_in: "Makan di sini", takeaway: "Bawa pulang", pickup: "Ambil sendiri", delivery: "Antar" };

function lineText(lines: KitchenLine[]) {
  return lines.map((l) => `${formatQty(l.quantity)}× ${l.size ? `${l.item_name} ${l.size}` : l.item_name || l.name}`).join(", ");
}

function TicketCard({
  ticket: t,
  now,
  busy,
  notice,
  onMove,
  onToggle,
}: {
  ticket: KitchenTicket;
  now: number;
  busy: boolean;
  notice?: string;
  onMove: (t: KitchenTicket, s: PrepState) => void;
  onToggle: (t: KitchenTicket, l: KitchenLine) => void;
}) {
  const waited = minutesSince(t.sold_at, now);
  // Relative, never a promised time: the longest-waiting tickets are marked,
  // the rest stay quiet.
  const level = waited >= 20 ? "long" : waited >= 10 ? "warm" : "fresh";
  const pending = t.lines.filter((l) => !l.done).length;
  const multi = t.lines.length > 1;
  const takeaway = t.order_type !== "dine_in";

  return (
    <article
      className="glass-card overflow-hidden p-0"
      style={level === "long" && t.state !== "ready" ? { boxShadow: "0 0 0 1.5px var(--warn), var(--shadow-card)" } : undefined}
      aria-label={`Pesanan ${t.code}`}
    >
      <div className="flex items-start justify-between gap-3 px-4 pb-2 pt-3.5">
        <div className="min-w-0">
          <p className="text-[30px] font-semibold leading-none tabular-nums tracking-[-0.025em]">{t.code}</p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span
              className="rounded-md px-2 py-0.5 text-[13px] font-semibold"
              style={takeaway ? { background: "var(--ink)", color: "var(--surface)" } : { background: "var(--fill)", color: "var(--ink)" }}
            >
              {t.order_type === "dine_in" ? t.table_label || "Makan di sini" : SERVICE[t.order_type] ?? t.order_type}
            </span>
            {t.parent_code && <span className="pill-warn text-[13px]">Tambahan untuk {t.parent_code}</span>}
            {t.guest_name && <span className="ink-soft truncate text-sm">{t.guest_name}</span>}
          </div>
          {t.delivery_address && <p className="ink-soft mt-1 text-sm">{t.delivery_address}</p>}
        </div>
        <div className="shrink-0 text-right">
          <p
            className={`text-[17px] tabular-nums ${level === "fresh" ? "font-medium" : "font-semibold"}`}
            style={level === "fresh" ? undefined : { color: "var(--warn)" }}
          >
            {waitLabel(waited)}
          </p>
          <p className="ink-faint text-xs tabular-nums">bayar {clockTime(t.sold_at)}</p>
        </div>
      </div>

      <ul className="hairline-t">
        {t.lines.map((l, i) => {
          const tickable = t.state !== "ready" && !!l.line_id && multi;
          const body = (
            <>
              <span className="w-9 shrink-0 text-[22px] font-semibold leading-tight tabular-nums">{formatQty(l.quantity)}×</span>
              <span className="min-w-0 flex-1">
                <span className={`block text-[19px] font-semibold leading-tight ${l.done && t.state === "preparing" ? "ink-faint line-through decoration-2" : ""}`}>
                  {l.item_name || l.name}
                  {l.size && <span className="font-medium"> · {l.size}</span>}
                </span>
                {l.modifiers.length > 0 && <span className="mt-0.5 block text-[16px] leading-snug">{l.modifiers.join(" · ")}</span>}
                {l.notes && (
                  <span className="mt-1 flex items-start gap-1.5 rounded-lg px-2 py-1 text-[15px] font-semibold leading-snug" style={{ background: "var(--warn-bg)", color: "var(--warn)" }}>
                    <IconNote className="mt-0.5 h-4 w-4 shrink-0" /> {l.notes}
                  </span>
                )}
              </span>
              {multi && t.state !== "ready" && (
                <span
                  aria-hidden
                  className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg"
                  style={l.done ? { background: "var(--good)", color: "#fff" } : { boxShadow: "inset 0 0 0 2px var(--hairline-strong)" }}
                >
                  {l.done && <IconCheck className="h-4 w-4" />}
                </span>
              )}
            </>
          );
          return (
            <li key={l.line_id ?? i} className={i > 0 ? "hairline-t" : ""}>
              {tickable ? (
                <button
                  role="checkbox"
                  aria-checked={l.done}
                  disabled={busy}
                  onClick={() => onToggle(t, l)}
                  className="flex min-h-[3.5rem] w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-[color:var(--row-hover)] active:bg-[color:var(--row-press)] disabled:cursor-wait"
                >
                  {body}
                </button>
              ) : (
                <div className="flex items-start gap-3 px-4 py-2.5">{body}</div>
              )}
            </li>
          );
        })}
      </ul>
      {t.note && (
        <p className="hairline-t px-4 py-2 text-[15px] font-semibold" style={{ color: "var(--warn)" }}>
          Catatan pesanan: {t.note}
        </p>
      )}

      <div className="hairline-t px-4 pb-4 pt-3">
        {notice && (
          <p role="alert" className="notice notice-warn mb-2 text-sm">
            {notice}
          </p>
        )}
        {t.state === "new" && (
          <button onClick={() => onMove(t, "preparing")} disabled={busy} className="btn-accent min-h-[3.25rem] w-full text-base">
            {busy ? "Menyimpan…" : "Mulai siapkan"}
          </button>
        )}
        {t.state === "preparing" && (
          <>
            <button
              onClick={() => onMove(t, "ready")}
              disabled={busy || (multi && pending > 0)}
              className="btn-accent min-h-[3.25rem] w-full text-base"
            >
              {busy ? "Menyimpan…" : multi && pending > 0 ? `${pending} item belum selesai` : "Siap diambil"}
            </button>
            {multi && pending > 0 && <p className="ink-faint mt-1.5 text-center text-xs">Ketuk tiap item yang sudah jadi</p>}
          </>
        )}
        {t.state === "ready" && (
          <>
            <button onClick={() => onMove(t, "done")} disabled={busy} className="btn-quiet min-h-[3.25rem] w-full text-base font-semibold" style={{ boxShadow: "0 0 0 1.5px var(--good)" }}>
              {busy ? "Menyimpan…" : "Sudah diserahkan"}
            </button>
            <p className="ink-faint mt-1.5 text-center text-xs">
              Siap sejak {t.state_since ? clockTime(t.state_since) : "tadi"}
              {t.state_by ? ` · ${t.state_by}` : ""}
            </p>
          </>
        )}
      </div>
    </article>
  );
}

function History({ tickets, now }: { tickets: KitchenTicket[]; now: number }) {
  if (tickets.length === 0) {
    return (
      <div className="mx-auto mt-16 max-w-md text-center">
        <p className="text-[22px] font-semibold tracking-[-0.02em]">Belum ada yang diserahkan</p>
        <p className="ink-soft mt-1">Pesanan yang sudah diserahkan dalam 12 jam terakhir tercatat di sini.</p>
      </div>
    );
  }
  return (
    <ul className="glass-card mx-auto max-w-3xl overflow-hidden p-0">
      {tickets.map((t, i) => (
        <li key={t.order_id} className={`flex items-start gap-4 px-5 py-3.5 ${i > 0 ? "hairline-t" : ""}`}>
          <p className="w-20 shrink-0 text-[19px] font-semibold tabular-nums">{t.code}</p>
          <div className="min-w-0 flex-1">
            <p className="text-[15px] leading-snug">{lineText(t.lines)}</p>
            <p className="ink-soft text-[13px]">
              {t.order_type === "dine_in" ? t.table_label || "Makan di sini" : SERVICE[t.order_type] ?? t.order_type}
              {t.guest_name ? ` · ${t.guest_name}` : ""}
              {t.parent_code ? ` · tambahan untuk ${t.parent_code}` : ""}
            </p>
          </div>
          <div className="shrink-0 text-right">
            {t.status !== "completed" ? (
              <span className="pill-bad">{t.status === "refunded" ? "Dikembalikan" : "Dibatalkan"}</span>
            ) : (
              <p className="text-sm font-medium tabular-nums">{t.state_since ? clockTime(t.state_since) : ""}</p>
            )}
            <p className="ink-faint text-xs">
              {t.state_since ? (minutesSince(t.state_since, now) < 1 ? "baru saja" : `${waitLabel(minutesSince(t.state_since, now))} lalu`) : ""}
              {t.state_by ? ` · ${t.state_by}` : ""}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}
