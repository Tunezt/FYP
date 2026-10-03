"use client";

import { useEffect, useRef, useState } from "react";
import QRCode from "qrcode";

// A print job's frozen document (prt-3), drawn as the slip it will be on paper.
// The same blocks a print bridge turns into ESC/POS; here they become an 80mm
// white slip for preview and for the browser's own print dialog.

export type PrintBlock = {
  t: "title" | "logo" | "banner" | "line" | "label" | "kv" | "rule" | "item" | "item_priced" | "total" | "text" | "note" | "qr";
  data?: string;
  width?: number; // logo (till-12): dots
  height?: number;
  bits?: string; // logo: base64 rows of width/8 bytes, MSB first, 1 = ink
  unit_price?: string; // item_priced (till-12): "15.000", shown as "2 x @15.000"
  text?: string;
  left?: string;
  right?: string;
  qty?: string;
  name?: string;
  size?: string | null;
  modifiers?: string[];
  notes?: string | null;
  amount?: string;
  flag?: string | null;
  style?: string;
  size_hint?: string;
  align?: string;
};

export type PrintDoc = { v: number; kind: string; blocks: PrintBlock[]; reprint?: number };

/** till-7: the WhatsApp receipt QR at the foot of a receipt, as on paper. */
function QrBlock({ data }: { data: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    QRCode.toDataURL(data, { width: 280, margin: 1, errorCorrectionLevel: "M" }).then(setSrc).catch(() => setSrc(null));
  }, [data]);
  // eslint-disable-next-line @next/next/no-img-element
  return src ? <img src={src} alt="Kode QR struk WhatsApp" className="mx-auto my-2 h-36 w-36" /> : null;
}

/** till-12: the logo bitmap exactly as the printer gets it (576 dots = the
 *  slip's 302 px). till-15: shrunk smoothly, the way 0.125 mm dots run together
 *  on paper; "pixelated" scaling dropped every other dot and looked broken. */
function LogoBlock({ width, height, bits, text }: { width: number; height: number; bits: string; text?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [ok, setOk] = useState(true);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    try {
      const raw = atob(bits);
      const rowBytes = width / 8;
      if (!width || width % 8 || raw.length !== rowBytes * height) throw new Error("bitmap");
      const img = ctx.createImageData(width, height);
      for (let y = 0; y < height; y++) {
        for (let x = 0; x < width; x++) {
          const on = (raw.charCodeAt(y * rowBytes + (x >> 3)) >> (7 - (x & 7))) & 1;
          const o = (y * width + x) * 4;
          img.data[o] = img.data[o + 1] = img.data[o + 2] = on ? 0 : 255;
          img.data[o + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
    } catch {
      setOk(false);
    }
  }, [width, height, bits]);
  if (!ok) return <p className="text-center text-[13px] font-bold tracking-wide">{text}</p>;
  return (
    <canvas
      ref={ref}
      width={width}
      height={height}
      role="img"
      aria-label={text ? `Logo ${text}` : "Logo"}
      className="mx-auto my-1 block"
      style={{ width: `${(width / 576) * 100}%`, height: "auto" }}
    />
  );
}

export function PrintDocument({ doc, id }: { doc: PrintDoc; id?: string }) {
  return (
    <div id={id} className="print-slip w-[302px] bg-white px-4 py-5 font-mono text-[13px] leading-[1.35] text-black">
      {doc.blocks.map((b, i) => {
        switch (b.t) {
          case "title":
            return (
              <p key={i} className="text-center text-[13px] font-bold tracking-wide">
                {b.text}
              </p>
            );
          case "logo":
            return b.width && b.height && b.bits ? (
              <LogoBlock key={i} width={b.width} height={b.height} bits={b.bits} text={b.text} />
            ) : (
              <p key={i} className="text-center text-[13px] font-bold tracking-wide">
                {b.text}
              </p>
            );
          case "label":
            return (
              <p key={i} className="my-1.5 bg-black py-1 text-center text-[17px] font-bold tracking-[0.2em] text-white">
                {b.text}
              </p>
            );
          case "banner":
            return (
              <p key={i} className="mt-1 text-center text-[30px] font-bold leading-tight">
                {b.text}
              </p>
            );
          case "line":
            return (
              <p key={i} className={`text-center ${b.style === "bold" ? "font-bold" : ""} ${(b as { size?: string }).size === "large" ? "text-[18px]" : ""}`}>
                {b.text}
              </p>
            );
          case "kv":
            return (
              <p key={i} className="flex justify-between gap-2">
                <span>{b.left}</span>
                <span className="text-right">{b.right}</span>
              </p>
            );
          case "rule":
            return b.style === "double" ? (
              <hr key={i} className="my-2 h-[5px] border-x-0 border-y border-solid border-black" />
            ) : (
              <hr key={i} className="my-2 border-dashed border-black" />
            );
          case "item":
            return (
              <div key={i} className="mb-2">
                {b.flag && <p className="text-[11px] font-bold">[{b.flag}]</p>}
                <p className="text-[17px] font-bold leading-tight">
                  {b.qty}× {b.name}
                  {b.size ? ` (${b.size})` : ""}
                </p>
                {(b.modifiers ?? []).map((m, j) => (
                  <p key={j} className="pl-5 text-[15px]">
                    - {m}
                  </p>
                ))}
                {b.notes && <p className="pl-5 text-[15px] font-bold">* {b.notes}</p>}
              </div>
            );
          case "item_priced":
            if (b.unit_price)
              return (
                <div key={i} className="mb-1.5">
                  <p className="font-bold">
                    {b.name}
                    {b.size ? ` (${b.size})` : ""}
                  </p>
                  <p className="flex justify-between gap-2 pl-3">
                    <span>
                      {b.qty} x @{b.unit_price}
                    </span>
                    <span className="shrink-0">{b.amount}</span>
                  </p>
                  {(b.modifiers ?? []).map((m, j) => (
                    <p key={j} className="pl-3 text-[12px]">
                      + {m}
                    </p>
                  ))}
                  {b.notes && <p className="pl-3 text-[12px]">* {b.notes}</p>}
                </div>
              );
            return (
              <div key={i} className="mb-1.5">
                <p className="flex justify-between gap-2">
                  <span>
                    {b.qty}× {b.name}
                    {b.size ? ` (${b.size})` : ""}
                  </span>
                  <span className="shrink-0">{b.amount}</span>
                </p>
                {(b.modifiers ?? []).map((m, j) => (
                  <p key={j} className="pl-4 text-[12px]">
                    + {m}
                  </p>
                ))}
                {b.notes && <p className="pl-4 text-[12px] italic">{b.notes}</p>}
              </div>
            );
          case "total":
            return (
              <p key={i} className="flex justify-between gap-2 text-[17px] font-bold">
                <span>{b.left}</span>
                <span>{b.right}</span>
              </p>
            );
          case "qr":
            return b.data ? <QrBlock key={i} data={b.data} /> : null;
          case "note":
            return (
              <p key={i} className="font-bold">
                {b.text}
              </p>
            );
          default:
            return (
              <p key={i} className={`${b.align === "center" ? "text-center" : ""} ${b.style === "bold" ? "font-bold" : ""}`}>
                {b.text}
              </p>
            );
        }
      })}
    </div>
  );
}
