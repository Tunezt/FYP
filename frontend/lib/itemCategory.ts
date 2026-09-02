/** Maps a free-text item name to a coarse product category, purely by keyword
 * matching on the Indonesian/Malay name the owner typed. Deterministic, local,
 * no network — used to pick a recognizable icon for stock rows instead of bare
 * initials. Returns null when nothing matches (caller falls back to initials).
 *
 * Order matters: the first rule whose keyword is a substring wins, so more
 * specific/likely categories are listed first (e.g. "kopi susu" → coffee, not
 * milk; "teh botol" → tea).
 */
export type ItemCategory =
  | "oil"
  | "rice"
  | "noodle"
  | "sugar"
  | "milk"
  | "coffee"
  | "tea"
  | "egg"
  | "gas"
  | "cleaning"
  | "cigarette"
  | "flour"
  | "water"
  | "sauce"
  | "snack"
  | "produce"
  | "bakery";

const RULES: { category: ItemCategory; keywords: string[] }[] = [
  { category: "coffee", keywords: ["kopi", "espresso", "cappuccino", "latte", "americano", "macchiato", "mocha"] },
  { category: "tea", keywords: ["teh", "tea"] },
  { category: "oil", keywords: ["minyak", "bimoli", "sania", "filma"] },
  { category: "rice", keywords: ["beras", "nasi"] },
  { category: "noodle", keywords: ["mie", "indomie", "bihun", "kwetiau", "spaghetti", "pasta"] },
  { category: "milk", keywords: ["susu", "skm", "kental manis", "frisian", "ultra", "milk"] },
  { category: "sugar", keywords: ["gula", "sugar"] },
  { category: "egg", keywords: ["telur", "telor"] },
  { category: "gas", keywords: ["gas", "elpiji", "lpg", "melon"] },
  {
    category: "cleaning",
    keywords: ["sabun", "deterjen", "detergen", "rinso", "sunlight", "pembersih", "pewangi", "pasta gigi", "odol", "shampo", "sampo", "pantene"],
  },
  { category: "cigarette", keywords: ["rokok", "surya", "sampoerna", "gudang garam", "marlboro", "djarum", "magnum"] },
  { category: "flour", keywords: ["tepung", "terigu", "maizena"] },
  { category: "water", keywords: ["aqua", "galon", "air mineral", "le minerale", "cleo", "air minum"] },
  { category: "sauce", keywords: ["kecap", "saus", "saos", "sambal", "cuka", "sasa", "royco", "masako"] },
  { category: "snack", keywords: ["kerupuk", "keripik", "biskuit", "wafer", "chiki", "snack", "permen", "coklat", "cokelat", "chitato", "roma"] },
  { category: "produce", keywords: ["sayur", "buah", "cabai", "cabe", "bawang", "tomat", "kentang", "wortel", "pisang", "jeruk"] },
  { category: "bakery", keywords: ["roti", "croissant", "kue", "donat", "donut", "pastry", "sandwich", "bolu", "muffin"] },
];

export function categorize(name: string): ItemCategory | null {
  const n = name.toLowerCase();
  for (const rule of RULES) {
    if (rule.keywords.some((k) => n.includes(k))) return rule.category;
  }
  return null;
}
