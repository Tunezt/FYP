import { IconSearch } from "@/components/icons";
import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="glass-card w-full max-w-md animate-scale-in px-8 py-10 text-center">
        <span className="surface-inset ink-faint mx-auto flex h-12 w-12 items-center justify-center rounded-2xl" style={{ boxShadow: "inset 0 0 0 1px var(--hairline)" }}>
          <IconSearch className="h-6 w-6" />
        </span>
        <h1 className="mt-4 text-xl font-semibold tracking-[-0.015em]">Halaman ini tidak ada</h1>
        <p className="ink-soft mt-2 text-sm">
          Mungkin tautannya salah ketik, atau halamannya sudah pindah.
        </p>
        <Link href="/" className="btn-accent mt-6 inline-flex px-6 py-3 text-sm">
          Kembali ke beranda
        </Link>
      </div>
    </main>
  );
}
