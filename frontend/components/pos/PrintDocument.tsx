"use client";

// A print job's frozen document (prt-3), drawn as the slip it will be on paper.
// The same blocks a print bridge turns into ESC/POS; here they become an 80mm
// white slip for preview and for the browser's own print dialog.

export type PrintBlock = {
  t: "title" | "banner" | "line" | "label" | "kv" | "rule" | "item" | "item_priced" | "total" | "text" | "note";
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
            return <hr key={i} className="my-2 border-dashed border-black" />;
          case "item":
            return (
              <div key={i} className="mb-2">
                {b.flag && <p className="text-[11px] font-bold">[{b.flag}]</p>}
                <p className="text-[17px] font-bold leading-tight">
                  {b.qty}× {b.name}
                  {b.size ? ` · ${b.size}` : ""}
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
            return (
              <div key={i} className="mb-1.5">
                <p className="flex justify-between gap-2">
                  <span>
                    {b.qty}× {b.name}
                    {b.size ? ` · ${b.size}` : ""}
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
              <p key={i} className="flex justify-between gap-2 text-[15px] font-bold">
                <span>{b.left}</span>
                <span>{b.right}</span>
              </p>
            );
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
