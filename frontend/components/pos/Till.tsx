"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, POS_PAIRING_KEY, POS_TOKEN_KEY } from "@/lib/api";
import { IconPlugOff } from "@/components/icons";
import { SellScreen } from "@/components/pos/SellScreen";
import { Center, PinPad, StaffPicker, type PosBusiness, type StaffLite } from "@/components/pos/StaffDoor";
type Screen =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "pick-staff"; business: PosBusiness }
  // `notice` (M15-T12) is why the last attempt failed when it was not simply
  // the wrong PIN — a cooldown that says how long to wait. Shaking the pad
  // silently for the fifth time is how a cashier decides the tablet is broken.
  | { kind: "pin"; business: PosBusiness; staff: StaffLite; pin: string; shake: boolean; notice?: string }
  | { kind: "sell"; staffName: string; businessName: string };

/** The till: who is on shift, their PIN, then the selling screen. Shown at
 *  /kasir for the café's paired tablet (kasir-1). */
export function Till({ pairingToken, onReset }: { pairingToken: string; onReset?: () => void }) {
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
          {/* kasir-3: a tablet set up for a café that has since been re-paired or
              rebuilt must not be stuck here: it can try again, or be set up anew. */}
          <div className="mt-6 flex flex-col gap-2">
            <button onClick={() => window.location.reload()} className="btn-quiet w-full py-3">
              Coba lagi
            </button>
            {onReset && (
              <button onClick={onReset} className="btn-accent w-full py-3">
                Siapkan ulang kasir di perangkat ini
              </button>
            )}
          </div>
        </div>
      </Center>
    );
  }

  if (screen.kind === "pick-staff") {
    return (
      <StaffPicker
        business={screen.business}
        title="Siapa yang jaga?"
        onPick={(s) => setScreen({ kind: "pin", business: screen.business, staff: s, pin: "", shake: false })}
      />
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
          if (screen.pin.length >= 4) return;
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

