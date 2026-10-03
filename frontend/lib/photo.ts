// A photo picked in the browser, made small enough to send (till-1). A phone
// camera gives 3-12 MB; the vision model reads a nota just as well at 1600 px,
// and the café's connection does not have to carry the rest.

export type Photo = {
  base64: string;        // without the data: prefix
  mime: "image/jpeg";
  previewUrl: string;    // object URL of the original, for the thumbnail
  bytes: number;         // size actually sent
};

const MAX_SIDE = 1600;

export async function readPhoto(file: File): Promise<Photo> {
  if (!file.type.startsWith("image/")) throw new Error("Pilih file foto (JPG atau PNG).");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("Foto ini tidak bisa dibuka di browser — coba foto ulang dengan kamera biasa.");
  }
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Browser ini tidak bisa mengolah foto.");
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
  const base64 = dataUrl.slice(dataUrl.indexOf(",") + 1);
  return { base64, mime: "image/jpeg", previewUrl: URL.createObjectURL(file), bytes: Math.round((base64.length * 3) / 4) };
}

/** Today's date as yyyy-mm-dd on this device (the till and the owner's phone
 *  are both in the café's timezone). */
export function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
