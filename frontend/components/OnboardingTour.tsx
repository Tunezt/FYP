"use client";

import { useEffect, useRef } from "react";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";
import { useOwnerMutation } from "@/lib/hooks";

/** First-run spotlight tour (driver.js). Fires once, when
 * businesses.onboarding_completed_at is null; marks completion server-side when
 * the owner finishes or dismisses it, so it never nags twice. */
export function OnboardingTour({ enabled }: { enabled: boolean }) {
  const mutate = useOwnerMutation();
  const started = useRef(false);

  useEffect(() => {
    if (!enabled || started.current) return;
    started.current = true;

    const complete = () => void mutate("/api/business/complete-onboarding").catch(() => {});

    const tour = driver({
      showProgress: true,
      progressText: "{{current}} / {{total}}",
      nextBtnText: "Lanjut",
      prevBtnText: "Kembali",
      doneBtnText: "Selesai ✓",
      overlayOpacity: 0.55,
      stagePadding: 8,
      stageRadius: 24,
      onDestroyed: complete,
      steps: [
        {
          element: "[data-tour='today']",
          popover: {
            title: "Angka hari ini",
            description:
              "Penjualan hari ini, langsung dari layar kasir. Grafiknya menunjukkan 30 hari terakhir — sekali lirik langsung kelihatan tren.",
          },
        },
        {
          element: "[data-tour='month']",
          popover: {
            title: "Untung bulan ini",
            description:
              "Semua penjualan dikurangi pengeluaran yang tercatat. Makin rajin foto nota ke WhatsApp, makin akurat angkanya.",
          },
        },
        {
          element: "[data-tour='attention']",
          popover: {
            title: "Perlu perhatian",
            description:
              "Sistem menghitung stok yang mau habis dan kejadian tidak biasa — otomatis, tiap malam. Yang penting juga dikirim ke WhatsApp kamu.",
          },
        },
        {
          element: "[data-tour='nav']",
          popover: {
            title: "Menu lainnya",
            description:
              "Riwayat penjualan, stok, keuangan, dan pengaturan staf/kasir ada di sini. Tapi ingat: sehari-hari cukup dari WhatsApp 😉",
          },
        },
      ],
    });

    // Let the data render first so the highlighted elements have real content.
    const timer = setTimeout(() => tour.drive(), 600);
    return () => {
      clearTimeout(timer);
      tour.destroy();
    };
  }, [enabled, mutate]);

  return null;
}
