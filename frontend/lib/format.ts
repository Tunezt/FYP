const idr = new Intl.NumberFormat("id-ID", {
  style: "currency",
  currency: "IDR",
  maximumFractionDigits: 0,
});

export function formatRupiah(amount: number | string): string {
  return idr.format(Number(amount));
}

const num = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });

export function formatQty(amount: number | string): string {
  return num.format(Number(amount));
}

/** Compact Rupiah for chart axes — "1,5jt", "1jt", "500rb", "0" (matches the
 * reference's Indonesian shorthand instead of a generic K/M abbreviation). */
export function formatCompactRupiah(amount: number): string {
  const abs = Math.abs(amount);
  const sign = amount < 0 ? "-" : "";
  if (abs === 0) return "0";
  if (abs >= 1_000_000) {
    const jt = abs / 1_000_000;
    return `${sign}${num.format(Number(jt.toFixed(jt >= 10 ? 0 : 1)))}jt`;
  }
  if (abs >= 1_000) {
    const rb = abs / 1_000;
    return `${sign}${num.format(Number(rb.toFixed(rb >= 10 ? 0 : 1)))}rb`;
  }
  return `${sign}${num.format(abs)}`;
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}
