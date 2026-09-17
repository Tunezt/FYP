"use client";

import { Line, LineChart, ResponsiveContainer } from "recharts";
import { IconArrowDown, IconArrowUp } from "@/components/icons";

/** One figure in the overview's key-figures strip: label (+ help) → the number
 * → how it moved → a quiet trend line. No card of its own: the strip is one
 * surface divided by hairlines, so three numbers read as one statement about
 * the business rather than three widgets. */
export function StatCard({
  label,
  help,
  value,
  deltaPct,
  deltaLabel,
  spark,
  footer,
}: {
  label: string;
  help?: React.ReactNode;
  value: React.ReactNode;
  deltaPct?: number | null;
  deltaLabel?: string;
  spark?: number[];
  footer?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-[132px] min-w-0 flex-col px-5 py-5 md:px-6">
      <p className="ink-soft flex items-center gap-1.5 text-[13px] font-medium">
        {label}
        {help}
      </p>
      <div className="mt-1.5 flex items-end justify-between gap-4">
        <p className="shrink-0 whitespace-nowrap text-[1.75rem] font-semibold leading-none tabular-nums tracking-[-0.025em] lg:text-[1.5rem] xl:text-[1.75rem]">
          {value}
        </p>
        {spark && spark.length > 1 && (
          <div className="h-8 min-w-0 max-w-24 flex-1 lg:hidden xl:block" aria-hidden>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={spark.map((v, i) => ({ i, v }))} margin={{ top: 2, left: 2, right: 2, bottom: 2 }}>
                <Line
                  type="monotone"
                  dataKey="v"
                  stroke="var(--chart-1)"
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
      <div className="mt-auto pt-3">
        {deltaPct !== undefined && deltaPct !== null && (
          <p className="flex items-center gap-1 text-[13px]">
            <span
              className={`inline-flex items-center gap-0.5 font-semibold tabular-nums ${
                deltaPct >= 0 ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"
              }`}
            >
              {deltaPct >= 0 ? <IconArrowUp className="h-3 w-3" /> : <IconArrowDown className="h-3 w-3" />}
              {Math.abs(deltaPct).toFixed(1).replace(".", ",")}%
            </span>
            {deltaLabel && <span className="ink-faint">{deltaLabel}</span>}
          </p>
        )}
        {footer}
      </div>
    </div>
  );
}
