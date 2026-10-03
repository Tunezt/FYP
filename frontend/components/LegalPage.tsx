import Link from "next/link";
import { BrandMark } from "@/components/ui";

// Public pages Meta requires before the WhatsApp app can be published: a
// privacy policy and data-deletion instructions. No login, no data fetching.

// Set in Vercel once Poernama has its own address. Until then the pages point
// people to the owner at the café, which is true for a single-shop system.
export const CONTACT_EMAIL = process.env.NEXT_PUBLIC_CONTACT_EMAIL?.trim() || null;

export const UPDATED = "1 Oktober 2026";

export function ContactLine() {
  return CONTACT_EMAIL ? (
    <>
      email ke{" "}
      <a className="font-semibold text-[color:var(--accent)] hover:underline" href={`mailto:${CONTACT_EMAIL}`}>
        {CONTACT_EMAIL}
      </a>
    </>
  ) : (
    <>langsung ke pemilik Poernama di kafe</>
  );
}

export function LegalPage({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <main className="min-h-screen px-4 py-10 sm:py-16">
      <div className="mx-auto w-full max-w-2xl">
        <div className="mb-8 flex justify-center">
          <BrandMark size="lg" />
        </div>
        <article className="glass-card px-6 py-7 sm:px-9 sm:py-9">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="ink-faint mt-1 text-sm">Terakhir diperbarui {UPDATED}</p>
          <div className="mt-6 space-y-5 text-[15px] leading-relaxed">{children}</div>
        </article>
        <nav className="ink-faint mt-6 flex justify-center gap-5 text-[13px]">
          <Link href="/privasi" className="hover:underline">
            Kebijakan privasi
          </Link>
          <Link href="/hapus-data" className="hover:underline">
            Penghapusan data
          </Link>
          <Link href="/login" className="hover:underline">
            Masuk
          </Link>
        </nav>
      </div>
    </main>
  );
}

export function Section({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-2 text-base font-semibold">{heading}</h2>
      <div className="ink-soft space-y-2">{children}</div>
    </section>
  );
}
