import type { Metadata } from "next";
import PosLayout from "../pos/layout";

// kasir-1: "Tambahkan ke layar utama" on the tablet makes this the Kasir app:
// its own icon, no address bar, the whole screen for the till.
export const metadata: Metadata = {
  title: "Kasir Poernama",
  manifest: "/kasir.webmanifest",
};

export default function KasirLayout({ children }: { children: React.ReactNode }) {
  return <PosLayout>{children}</PosLayout>;
}
