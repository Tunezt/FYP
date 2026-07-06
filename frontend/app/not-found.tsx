import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="glass-card animate-scale-in max-w-md px-8 py-10 text-center">
        <p className="text-4xl">🧭</p>
        <h1 className="mt-4 text-xl font-bold">Halaman ini tidak ada</h1>
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
