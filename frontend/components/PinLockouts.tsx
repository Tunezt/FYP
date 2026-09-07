"use client";

/** M15-T12 — what the owner sees when somebody is being throttled.
 *
 * The guard is an escalating cooldown rather than a lock precisely so the till
 * keeps trading, but a cashier who has genuinely forgotten their PIN at the
 * start of a rush cannot wait fifteen minutes either. The owner is the only
 * person who can tell "forgot their PIN" from "trying everyone else's", so the
 * owner gets the list and the override.
 *
 * Only subjects still inside the counting window appear. Everything else is
 * history, and the history — every single failed attempt, whether or not it
 * ever reached a threshold — is in `request_logs`.
 */

import { useEffect, useState } from "react";
import { useOwnerData, useOwnerMutation } from "@/lib/hooks";
import { timeLabel } from "@/lib/dates";
import type { PinLockoutRow } from "@/lib/types";
import { HelpTip } from "@/components/HelpTip";
import { Plate, Skeleton } from "@/components/ui";

const SCOPE_LABEL: Record<string, string> = {
  pos_login: "PIN kasir",
  pos_device: "Perangkat kasir",
  manager_pin: "PIN manajer",
};

/** Counts down without a second request: the server said when it ends. */
function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

function waitLabel(until: string, now: number): string {
  const seconds = Math.max(0, Math.ceil((new Date(until).getTime() - now) / 1000));
  if (seconds === 0) return "selesai";
  if (seconds < 60) return `${seconds} detik lagi`;
  return `${Math.ceil(seconds / 60)} menit lagi`;
}

export function PinLockouts({ tz }: { tz?: string }) {
  const lockouts = useOwnerData<PinLockoutRow[]>("/api/pin-lockouts");
  const mutate = useOwnerMutation();
  const [clearing, setClearing] = useState<string | null>(null);
  const rows = lockouts.data ?? [];
  const now = useNow(rows.some((r) => r.locked_now));

  async function clear(id: string) {
    setClearing(id);
    try {
      await mutate(`/api/pin-lockouts/${id}`, undefined, "DELETE");
      lockouts.reload();
    } finally {
      setClearing(null);
    }
  }

  // Nothing being counted is the normal state, and an empty box every day
  // trains the owner to stop looking at this part of the page.
  if (!lockouts.loading && rows.length === 0) return null;

  return (
    <section>
      <h2 className="mb-2 flex items-center gap-2 text-base font-bold">
        Percobaan PIN
        <HelpTip title="Kenapa ada daftar ini">
          Kalau PIN salah berkali-kali, kasir diminta menunggu sebentar sebelum boleh mencoba lagi —
          30 detik, lalu 2 menit, lalu 15 menit. Ini supaya PIN 4 angka tidak bisa ditebak satu per
          satu oleh orang yang memegang tablet seharian. Kasir tetap bisa jualan; yang ditahan cuma
          percobaan PIN-nya. Kalau memang cuma lupa, buka blokirnya di sini. Kalau yang muncul
          adalah PIN manajer atau nama yang tidak kamu duga, tanyakan hari itu juga.
        </HelpTip>
      </h2>
      {lockouts.loading && !lockouts.data ? (
        <Skeleton className="h-20" />
      ) : (
        <Plate className="px-6 py-4">
          <ul className="space-y-3">
            {rows.map((row) => (
              <li key={row.id} className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold">
                    {row.who}
                    <span className="ink-faint font-normal"> · {SCOPE_LABEL[row.scope] ?? row.scope}</span>
                  </p>
                  <p className="ink-faint text-xs">
                    {row.failures}× salah · terakhir {timeLabel(new Date(row.last_failed_at), tz)}
                    {row.locked_now && row.locked_until
                      ? ` · tunggu ${waitLabel(row.locked_until, now)}`
                      : " · belum ditahan"}
                  </p>
                </div>
                <button
                  onClick={() => clear(row.id)}
                  disabled={clearing === row.id}
                  className="btn-quiet shrink-0 px-3 py-1.5 text-xs"
                >
                  {clearing === row.id ? "…" : "Buka blokir"}
                </button>
              </li>
            ))}
          </ul>
        </Plate>
      )}
    </section>
  );
}
