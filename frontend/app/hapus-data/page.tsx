import type { Metadata } from "next";
import Link from "next/link";
import { ContactLine, LegalPage, Section } from "@/components/LegalPage";

export const metadata: Metadata = {
  title: "Penghapusan data · Poernama",
  description: "Cara meminta data kamu dihapus dari sistem Poernama.",
};

export default function DataDeletionPage() {
  return (
    <LegalPage title="Penghapusan data">
      <p>
        Kamu bisa meminta data tentang dirimu dihapus dari sistem Poernama. Ini berlaku untuk pelanggan,
        tamu yang memesan lewat QR, staf, dan siapa pun yang pernah mengirim pesan WhatsApp ke nomor
        Poernama.
      </p>

      <Section heading="Cara meminta">
        <ol className="list-decimal space-y-1.5 pl-5">
          <li>
            Kirim permintaan <ContactLine />.
          </li>
          <li>Sebutkan nama dan nomor HP yang kamu pakai, supaya datanya bisa ditemukan.</li>
          <li>Kami mengonfirmasi bahwa nomor itu memang milikmu sebelum menghapus apa pun.</li>
        </ol>
      </Section>

      <Section heading="Yang dihapus">
        <p>
          Nama, nomor HP, alamat, poin pelanggan, dan pesan WhatsApp beserta fotonya, paling lambat 30
          hari setelah permintaan dikonfirmasi.
        </p>
      </Section>

      <Section heading="Yang tetap disimpan">
        <p>
          Angka transaksinya (apa yang terjual, kapan, dan berapa) tetap ada di pembukuan kafe, tetapi
          tidak lagi terhubung ke nama atau nomormu. Pembukuan usaha memang harus lengkap.
        </p>
      </Section>

      <p className="ink-soft">
        Detail data yang disimpan ada di{" "}
        <Link href="/privasi" className="font-semibold text-[color:var(--accent)] hover:underline">
          kebijakan privasi
        </Link>
        .
      </p>

      <p className="ink-faint hairline-t pt-5 text-[13px]" lang="en">
        In English: to have your data deleted, contact Poernama as above with the name and phone number
        you used. After confirming the number is yours, your name, phone number, address, loyalty points
        and WhatsApp messages with their photos are deleted within 30 days. Transaction amounts stay in
        the café&apos;s books, no longer linked to you.
      </p>
    </LegalPage>
  );
}
