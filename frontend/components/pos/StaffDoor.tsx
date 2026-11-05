"use client";

import { useEffect, useRef } from "react";
import { IconBackspace } from "@/components/icons";
import { initials } from "@/lib/format";
import { Wordmark } from "@/components/Wordmark";

export type StaffLite = { id: string; name: string; role: string };
export type PosBusiness = { business_name: string; staff: StaffLite[] };

/** The way into a staff screen. The till (/kasir) and the kitchen screen open
 *  the same way: who is on, then their PIN on the pad. */

export function Center({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center">{children}</main>;
}

/** Who is on: the café's sign, the question, and one card per person. */
export function StaffPicker({
  business,
  title,
  note,
  onPick,
}: {
  business: PosBusiness;
  title: string;
  note?: string;
  onPick: (s: StaffLite) => void;
}) {
  return (
    <Center>
      <div className="w-full max-w-2xl animate-fade-up px-6">
        <Wordmark className="mx-auto w-[168px]" title={business.business_name} />
        <h1 className="mt-9 text-center text-[2rem] font-semibold tracking-[-0.025em]">{title}</h1>
        {note && <p className="ink-soft mt-1 text-center">{note}</p>}
        <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-3">
          {business.staff.map((s, i) => (
            <button
              key={s.id}
              onClick={() => onPick(s)}
              className="glass-card flex flex-col items-center gap-3 px-4 py-8 transition-[transform,box-shadow] duration-150 hover:shadow-key active:scale-[0.98]"
              style={{ animationDelay: `${i * 60}ms` }}
            >
              <span className="surface-inset ink-soft flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
                {initials(s.name)}
              </span>
              <span className="text-[17px] font-semibold">{s.name}</span>
              {s.role === "owner" && <span className="pill-good">pemilik</span>}
            </button>
          ))}
        </div>
      </div>
    </Center>
  );
}

export function PinPad({
  staff,
  pin,
  shake,
  notice,
  onDigit,
  onDelete,
  onBack,
}: {
  staff: StaffLite;
  pin: string;
  shake: boolean;
  notice?: string;
  onDigit: (d: string) => void;
  onDelete: () => void;
  onBack: () => void;
}) {
  // A tablet with a keyboard cover, or the owner at a laptop, can type the PIN
  // too: digits fill, Backspace takes one back, Escape goes back to the names.
  const keys = useRef({ onDigit, onDelete, onBack });
  keys.current = { onDigit, onDelete, onBack };
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^[0-9]$/.test(e.key)) keys.current.onDigit(e.key);
      else if (e.key === "Backspace") keys.current.onDelete();
      else if (e.key === "Escape") keys.current.onBack();
      else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <Center>
      <div className="w-full max-w-sm animate-scale-in px-6 text-center">
        <span className="surface-inset ink-soft mx-auto flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
          {initials(staff.name)}
        </span>
        <h1 className="mt-4 text-2xl font-semibold tracking-[-0.02em]">Halo, {staff.name}</h1>
        {notice ? (
          <p className="mx-auto mt-3 max-w-xs notice notice-bad" role="alert">
            {notice}
          </p>
        ) : (
          <p className="ink-soft mt-1">Masukkan PIN 4 angka</p>
        )}

        <div className={`mt-6 flex justify-center gap-4 ${shake ? "shake" : ""}`} role="img" aria-label={`${pin.length} dari 4 angka`}>
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className="pin-dot" data-filled={i < pin.length} />
          ))}
        </div>

        <div className="mx-auto mt-8 grid max-w-[18rem] grid-cols-3 gap-x-5 gap-y-4">
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <PinKey key={d} label={d} onPress={() => onDigit(d)} />
          ))}
          <button
            onClick={onBack}
            className="rounded-2xl py-5 text-[15px] font-medium text-[color:var(--ink-soft)] transition-colors hover:text-[color:var(--ink)] active:opacity-60"
          >
            batal
          </button>
          <PinKey label="0" onPress={() => onDigit("0")} />
          <button
            onClick={onDelete}
            aria-label="hapus"
            className="ink-soft flex items-center justify-center rounded-2xl py-5 transition-colors hover:text-[color:var(--ink)] active:opacity-60"
          >
            <IconBackspace className="h-7 w-7" />
          </button>
        </div>
      </div>
    </Center>
  );
}

function PinKey({ label, onPress }: { label: string; onPress: () => void }) {
  return (
    <button
      onClick={onPress}
      className="rounded-full bg-[color:var(--surface)] py-5 text-[26px] font-normal tabular-nums shadow-key transition-[transform,background-color] duration-100 hover:bg-[color:var(--surface-inset)] active:scale-95 active:bg-[color:var(--row-press)]"
    >
      {label}
    </button>
  );
}
