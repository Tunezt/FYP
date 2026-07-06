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

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}
