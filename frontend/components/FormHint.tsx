/** The line under a disabled primary button that says what is still missing
 *  (till-2). A button that is silently grey reads as broken; one that says
 *  "Isi jumlah dan alasan" tells the cashier what to do next. Renders nothing
 *  once the form is ready. */
export function FormHint({ missing, className = "" }: { missing: string | null | false; className?: string }) {
  if (!missing) return null;
  return (
    <p className={`ink-soft mt-1.5 text-center text-[13px] ${className}`} role="status" aria-live="polite">
      {missing}
    </p>
  );
}

/** "Isi jumlah dan alasan" from ["jumlah", "alasan"]: the parts still empty,
 *  joined the way a person would say them. */
export function missingText(verb: string, parts: (string | false | null | undefined)[]): string | null {
  const left = parts.filter((p): p is string => !!p);
  if (left.length === 0) return null;
  const list = left.length === 1 ? left[0] : `${left.slice(0, -1).join(", ")} dan ${left[left.length - 1]}`;
  return `${verb} ${list}`;
}
