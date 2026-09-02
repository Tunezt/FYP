"use client";

import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { Plate } from "@/components/ui";
import { IconArrowDown, IconArrowUp } from "@/components/icons";

/** Reference-grammar stat card: icon tile → label (+ help) → display number →
 * delta chip → mini sparkline. Lives on a quiet plate — the hero surface of a
 * page is elsewhere. */
export function StatCard({
  icon,
  label,
  help,
  value,
  deltaPct,
  deltaLabel,
  spark,
  footer,
}: {
  icon: React.ReactNode;
  label: string;
  help?: React.ReactNode;
  value: React.ReactNode;
  deltaPct?: number | null;
  deltaLabel?: string;
  spark?: number[];
  footer?: React.ReactNode;
}) {
  return (
    <Plate className="flex min-h-[172px] flex-col overflow-hidden">
      <div className="flex-1 px-5 pt-5">
        <span
          className="ink-soft flex h-10 w-10 items-center justify-center rounded-xl"
          style={{ background: "var(--hairline)" }}
        >
          {icon}
        </span>
        <p className="ink-soft mt-3 flex items-center gap-1.5 text-[13px] font-medium">
          {label}
          {help}
        </p>
        <p className="mt-0.5 text-[1.75rem] font-bold leading-tight tabular-nums tracking-tight">
          {value}
        </p>
        {deltaPct !== undefined && deltaPct !== null && (
          <p
            className={`mt-1 flex items-center gap-1 text-xs font-semibold ${
              deltaPct >= 0 ? "text-[color:var(--good)]" : "text-[color:var(--bad)]"
            }`}
          >
            {deltaPct >= 0 ? <IconArrowUp className="h-3 w-3" /> : <IconArrowDown className="h-3 w-3" />}
            {Math.abs(deltaPct).toFixed(1)}%
            {deltaLabel && <span className="ink-faint font-medium">{deltaLabel}</span>}
          </p>
        )}
        {footer}
      </div>
      {spark && spark.length > 1 && (
        <div className="h-12 w-full" aria-hidden>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={spark.map((v, i) => ({ i, v }))}
              margin={{ top: 4, left: 0, right: 0, bottom: 0 }}
            >
              <defs>
                <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.22} />
                  <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <Area
                type="monotone"
                dataKey="v"
                stroke="var(--chart-1)"
                strokeWidth={2}
                fill="url(#spark-fill)"
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Plate>
  );
}
