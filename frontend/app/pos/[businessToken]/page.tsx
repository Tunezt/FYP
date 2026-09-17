"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api, ApiError, POS_PAIRING_KEY, POS_TOKEN_KEY } from "@/lib/api";
import { IconBackspace, IconPlugOff } from "@/components/icons";
import { initials } from "@/lib/format";
import { SellScreen } from "@/components/pos/SellScreen";

type StaffLite = { id: string; name: string; role: string };
type PosBusiness = { business_name: string; staff: StaffLite[] };
type Screen =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "pick-staff"; business: PosBusiness }
  // `notice` (M15-T12) is why the last attempt failed when it was not simply
  // the wrong PIN — a cooldown that says how long to wait. Shaking the pad
  // silently for the fifth time is how a cashier decides the tablet is broken.
  | { kind: "pin"; business: PosBusiness; staff: StaffLite; pin: string; shake: boolean; notice?: string }
  | { kind: "sell"; staffName: string; businessName: string };

export default function PosPage() {
  const params = useParams<{ businessToken: string }>();
  const pairingToken = params.businessToken;
  const [screen, setScreen] = useState<Screen>({ kind: "loading" });
  const [posToken, setPosToken] = useState<string | null>(null);

  useEffect(() => {
    // Persist the pairing so this kiosk always boots into this business.
    localStorage.setItem(POS_PAIRING_KEY, pairingToken);
    api<PosBusiness>(`/pos/business/${pairingToken}`)
      .then((business) => setScreen({ kind: "pick-staff", business }))
      .catch((e: unknown) =>
        setScreen({
          kind: "error",
          message: e instanceof ApiError ? e.detail : "Tidak bisa terhubung ke server.",
        })
      );
  }, [pairingToken]);

  const tryPin = useCallback(
    async (staff: StaffLite, business: PosBusiness, pin: string) => {
      try {
        const res = await api<{ token: string; staff_name: string; business_name: string }>(
          "/pos/login",
          { body: { pairing_token: pairingToken, staff_id: staff.id, pin } }
        );
        localStorage.setItem(POS_TOKEN_KEY, res.token);
        setPosToken(res.token);
        setScreen({ kind: "sell", staffName: res.staff_name, businessName: res.business_name });
      } catch (e: unknown) {
        if (e instanceof ApiError && e.status === 401 && e.detail.startsWith("Perangkat ini")) {
          // Re-paired while this kiosk was open (M15-T8): no PIN will work here
          // again, so stop shaking the pad and say so.
          setScreen({ kind: "error", message: e.detail });
          return;
        }
        // A cooldown (M15-T12) is not a wrong PIN, and the difference matters:
        // the right PIN will not work either until the wait is over, so say so
        // rather than letting the cashier keep trying the one they know.
        const notice = e instanceof ApiError && e.status === 429 ? e.detail : undefined;
        setScreen({ kind: "pin", business, staff, pin: "", shake: true, notice });
        setTimeout(
          () =>
            setScreen((s) => (s.kind === "pin" ? { ...s, shake: false } : s)),
          500
        );
      }
    },
    [pairingToken]
  );

  if (screen.kind === "loading") {
    return (
      <Center>
        <p className="ink-soft animate-pulse text-lg">Menyiapkan kasir…</p>
      </Center>
    );
  }

  if (screen.kind === "error") {
    return (
      <Center>
        <div className="glass-card mx-6 max-w-md px-8 py-10 text-center">
          <span className="surface-inset ink-faint mx-auto flex h-12 w-12 items-center justify-center rounded-2xl" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
            <IconPlugOff className="h-6 w-6" />
          </span>
          <h1 className="mt-4 text-xl font-semibold tracking-[-0.015em]">Kasir belum terhubung</h1>
          <p className="ink-soft mt-2">{screen.message}</p>
        </div>
      </Center>
    );
  }

  if (screen.kind === "pick-staff") {
    return (
      <Center>
        <div className="w-full max-w-2xl animate-fade-up px-6">
          <p className="ink-soft text-center text-[15px] font-medium">
            {screen.business.business_name}
          </p>
          <h1 className="mt-1 text-center text-[2rem] font-semibold tracking-[-0.025em]">Siapa yang jaga?</h1>
          <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-3">
            {screen.business.staff.map((s, i) => (
              <button
                key={s.id}
                onClick={() =>
                  setScreen({ kind: "pin", business: screen.business, staff: s, pin: "", shake: false })
                }
                className="glass-card flex flex-col items-center gap-3 px-4 py-8 transition-[transform,box-shadow] duration-150 hover:shadow-key active:scale-[0.98]"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <span className="surface-inset ink-soft flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
                  {initials(s.name)}
                </span>
                <span className="text-[17px] font-semibold">{s.name}</span>
                {s.role === "owner" && (
                  <span className="pill-good">
                    pemilik
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      </Center>
    );
  }

  if (screen.kind === "pin") {
    return (
      <PinPad
        staff={screen.staff}
        pin={screen.pin}
        shake={screen.shake}
        notice={screen.notice}
        onBack={() => setScreen({ kind: "pick-staff", business: screen.business })}
        onDigit={(d) => {
          const pin = screen.pin + d;
          if (pin.length === 4) {
            void tryPin(screen.staff, screen.business, pin);
            setScreen({ ...screen, pin });
          } else {
            setScreen({ ...screen, pin });
          }
        }}
        onDelete={() => setScreen({ ...screen, pin: screen.pin.slice(0, -1) })}
      />
    );
  }

  return (
    <SellScreen
      posToken={posToken}
      pairingToken={pairingToken}
      staffName={screen.staffName}
      businessName={screen.businessName}
      onLock={() => {
        localStorage.removeItem(POS_TOKEN_KEY);
        setPosToken(null);
        api<PosBusiness>(`/pos/business/${pairingToken}`)
          .then((business) => setScreen({ kind: "pick-staff", business }))
          .catch((e: unknown) =>
            // A device the owner has re-paired (M15-T8) answers with its own
            // message; showing "Koneksi terputus" would send staff to check
            // the WiFi for something the WiFi cannot fix.
            setScreen({
              kind: "error",
              message: e instanceof ApiError ? e.detail : "Koneksi terputus.",
            })
          );
      }}
    />
  );
}

function Center({ children }: { children: React.ReactNode }) {
  return <main className="flex min-h-screen items-center justify-center">{children}</main>;
}

function PinPad({
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
  return (
    <Center>
      <div className="w-full max-w-sm animate-scale-in px-6 text-center">
        <span className="surface-inset ink-soft mx-auto flex h-16 w-16 items-center justify-center rounded-full text-xl font-semibold" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
          {initials(staff.name)}
        </span>
        <h1 className="mt-4 text-2xl font-semibold tracking-[-0.02em]">Halo, {staff.name}</h1>
        {notice ? (
          <p
            className="mx-auto mt-3 max-w-xs notice notice-bad"
          >
            {notice}
          </p>
        ) : (
          <p className="ink-soft mt-1">Masukkan PIN 4 angka</p>
        )}

        <div
          className={`mt-6 flex justify-center gap-4 ${shake ? "animate-[shake_0.4s_ease-in-out]" : ""}`}
          style={
            shake
              ? { animation: "shake 0.4s ease-in-out" }
              : undefined
          }
        >
          {[0, 1, 2, 3].map((i) => (
            <span
              key={i}
              className={`h-3.5 w-3.5 rounded-full border-[1.5px] transition-all duration-150 ${
                i < pin.length
                  ? "border-[color:var(--accent)] bg-[color:var(--accent-fill)]"
                  : "border-[color:var(--ink-faint)]"
              }`}
            />
          ))}
        </div>
        <style>{`@keyframes shake { 0%,100%{transform:translateX(0)} 20%{transform:translateX(-10px)} 40%{transform:translateX(10px)} 60%{transform:translateX(-6px)} 80%{transform:translateX(6px)} }`}</style>

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
