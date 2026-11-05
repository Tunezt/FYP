"use client";

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { formatCompactRupiah, formatRupiah } from "@/lib/format";
import type { PnlMonth, TrendPoint } from "@/lib/types";

/* The product's charts, drawn here rather than by a charting library: one
 * series of ink on porcelain, hairline grid, and a reading layer that works
 * the same with a mouse, a finger and the arrow keys. Every entrance is a CSS
 * animation on real geometry, so a chart is complete the moment it is in the
 * page and only *arrives* animated (see "Charts" in globals.css). */

function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    read();
    const observer = new ResizeObserver(read);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

/** Round axis values: 0 / 350rb / 700rb, never 0 / 331.250 / 662.500. */
function niceScale(max: number, steps = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / steps;
  const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw;
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let v = 0; v <= top + step / 2; v += step) ticks.push(v);
  return ticks;
}

/** A smooth line through the points that never overshoots them (monotone
 * cubic, Fritsch–Butland tangents): a day with no sales touches zero, it does
 * not dip under it. With `splitAt`, the path is returned in two parts joined
 * by "|", the second starting at that point. */
function monotonePath(pts: [number, number][], splitAt = -1): string {
  const n = pts.length;
  if (n === 0) return "";
  if (n === 1) return `M${pts[0][0]},${pts[0][1]}`;
  const slope: number[] = [];
  for (let i = 0; i < n - 1; i++) slope.push((pts[i + 1][1] - pts[i][1]) / (pts[i + 1][0] - pts[i][0]));
  const tangent: number[] = [slope[0]];
  for (let i = 1; i < n - 1; i++) {
    const a = slope[i - 1];
    const b = slope[i];
    tangent.push(a * b <= 0 ? 0 : (2 * a * b) / (a + b));
  }
  tangent.push(slope[n - 2]);
  let d = `M${pts[0][0].toFixed(2)},${pts[0][1].toFixed(2)}`;
  for (let i = 0; i < n - 1; i++) {
    if (i === splitAt && i > 0) d += `|M${pts[i][0].toFixed(2)},${pts[i][1].toFixed(2)}`;
    const [x0, y0] = pts[i];
    const [x1, y1] = pts[i + 1];
    const third = (x1 - x0) / 3;
    d += `C${(x0 + third).toFixed(2)},${(y0 + tangent[i] * third).toFixed(2)},${(x1 - third).toFixed(2)},${(
      y1 -
      tangent[i + 1] * third
    ).toFixed(2)},${x1.toFixed(2)},${y1.toFixed(2)}`;
  }
  return d;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

const dayShort = (iso: string) => new Date(iso).toLocaleDateString("id-ID", { day: "numeric", month: "short" });
const dayFull = (iso: string) =>
  new Date(iso).toLocaleDateString("id-ID", { weekday: "short", day: "numeric", month: "short" });

/** Sales over time. Point at it (or drag a finger, or use the arrow keys) and
 * a lit point travels the line to the nearest day and says what that day
 * took. The last point is today: it carries the lamp. */
export function TrendChart({
  points,
  axis = true,
  label = "Grafik penjualan per hari",
}: {
  points: TrendPoint[];
  /** Draw the value axis on the left. */
  axis?: boolean;
  label?: string;
}) {
  const [ref, { w, h }] = useSize<HTMLDivElement>();
  const gradient = useId();
  const [active, setActive] = useState<number | null>(null);
  const n = points.length;

  const geo = useMemo(() => {
    if (w === 0 || h === 0 || n === 0) return null;
    const left = axis ? 52 : 20;
    const right = 24;
    const top = 18;
    const bottom = 34;
    const x0 = left + 12;
    const x1 = w - right - 12;
    const yBase = h - bottom;
    const ticks = niceScale(Math.max(...points.map((p) => p.revenue)));
    const yMax = ticks[ticks.length - 1] || 1;
    const x = (i: number) => (n === 1 ? (x0 + x1) / 2 : x0 + ((x1 - x0) * i) / (n - 1));
    const y = (v: number) => yBase - ((yBase - top) * v) / yMax;
    const xy = points.map((p, i) => [x(i), y(p.revenue)] as [number, number]);
    // Today is still being written: the stretch that leads to it is drawn
    // broken, so a quiet morning never reads as a collapse.
    const [line, today = ""] = monotonePath(xy, n - 2).split("|");
    const whole = monotonePath(xy);
    const area = `${whole}L${xy[n - 1][0].toFixed(2)},${yBase}L${xy[0][0].toFixed(2)},${yBase}Z`;
    // As many date labels as fit, counted back from today so today is always named.
    const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((x1 - x0) / 68))));
    const labels = points.map((_, i) => i).filter((i) => (n - 1 - i) % every === 0);
    return { left, right, top, yBase, x0, x1, ticks, y, xy, line, today, area, labels };
  }, [w, h, n, points, axis]);

  // A new period is a new drawing: forget where the pointer was.
  const identity = `${n}:${points[0]?.date ?? ""}`;
  useEffect(() => setActive(null), [identity]);

  function nearest(clientX: number): number {
    if (!geo || !ref.current) return 0;
    const px = clientX - ref.current.getBoundingClientRect().left;
    if (n === 1) return 0;
    return clamp(Math.round(((px - geo.x0) / (geo.x1 - geo.x0)) * (n - 1)), 0, n - 1);
  }

  function onKey(e: React.KeyboardEvent) {
    const at = active ?? n - 1;
    const next =
      e.key === "ArrowLeft" ? at - 1 : e.key === "ArrowRight" ? at + 1 : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : null;
    if (e.key === "Escape") setActive(null);
    if (next === null) return;
    e.preventDefault();
    setActive(clamp(next, 0, n - 1));
  }

  const point = active !== null ? points[active] : null;
  const at = active !== null && geo ? geo.xy[active] : null;
  const last = geo ? geo.xy[n - 1] : null;

  return (
    <div
      ref={ref}
      className="chart relative h-full w-full touch-pan-y select-none"
      role="group"
      aria-label={label}
      tabIndex={0}
      onKeyDown={onKey}
      onFocus={(e) => e.target === e.currentTarget && e.currentTarget.matches(":focus-visible") && setActive(n - 1)}
      onBlur={() => setActive(null)}
      onPointerMove={(e) => setActive(nearest(e.clientX))}
      onPointerDown={(e) => setActive(nearest(e.clientX))}
      onPointerLeave={() => setActive(null)}
      onPointerCancel={() => setActive(null)}
    >
      {geo && (
        <svg width={w} height={h} className="absolute inset-0 overflow-visible" aria-hidden>
          <defs>
            <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--chart-1)" stopOpacity={0.13} />
              <stop offset="100%" stopColor="var(--chart-1)" stopOpacity={0} />
            </linearGradient>
          </defs>

          {geo.ticks.map((t) => (
            <g key={t}>
              <line x1={geo.left} x2={w - geo.right} y1={geo.y(t)} y2={geo.y(t)} className="chart-grid" />
              {axis && (
                <text x={geo.left - 10} y={geo.y(t)} dy="0.32em" textAnchor="end" className="chart-tick">
                  {formatCompactRupiah(t)}
                </text>
              )}
            </g>
          ))}
          {geo.labels.map((i) => (
            <text key={points[i].date} x={geo.xy[i][0]} y={geo.yBase + 22} textAnchor="middle" className="chart-tick">
              {dayShort(points[i].date)}
            </text>
          ))}

          {/* Keyed on the period, so choosing another one draws a new line. */}
          <g key={identity}>
            {n > 1 && <path d={geo.area} fill={`url(#${gradient})`} className="chart-area" />}
            {n > 1 && <path d={geo.line} pathLength={1} className="chart-line" />}
            {geo.today && <path d={geo.today} className="chart-line-today" />}
            {last && (
              <g className="chart-now" style={{ transform: `translate(${last[0]}px, ${last[1]}px)` }}>
                <circle r={5} className="chart-now-halo" />
                <circle r={5} className="chart-now-dot" />
              </g>
            )}
          </g>

          {at && (
            <>
              <line
                x1={0}
                x2={0}
                y1={geo.top - 6}
                y2={geo.yBase}
                className="chart-cursor"
                style={{ transform: `translateX(${at[0]}px)` }}
              />
              <circle r={4.5} className="chart-point" style={{ transform: `translate(${at[0]}px, ${at[1]}px)` }} />
            </>
          )}
        </svg>
      )}

      {geo && point && at && (
        <div
          className="chart-tip chart-tip-float"
          role="status"
          style={{
            transform: `translate(${clamp(at[0], 76, Math.max(76, w - 76))}px, ${Math.max(at[1] - 18, 66)}px) translate(-50%, -100%)`,
          }}
        >
          <p className="ink-soft">{dayFull(point.date)}</p>
          <p className="text-sm font-semibold tabular-nums">{formatRupiah(point.revenue)}</p>
          <p className="ink-faint">{point.transactions} transaksi</p>
        </div>
      )}
    </div>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
const MONTHS_LONG = [
  "Januari",
  "Februari",
  "Maret",
  "April",
  "Mei",
  "Juni",
  "Juli",
  "Agustus",
  "September",
  "Oktober",
  "November",
  "Desember",
];
const monthIndex = (ym: string) => Number(ym.slice(5)) - 1;
const compact = new Intl.NumberFormat("id-ID", { notation: "compact" });

/** A column with a rounded cap and a square foot, standing on the baseline. */
function column(x: number, yBase: number, width: number, height: number): string {
  const r = Math.min(4, height, width / 2);
  const top = yBase - height;
  return `M${x},${yBase}V${top + r}Q${x},${top} ${x + r},${top}H${x + width - r}Q${x + width},${top} ${x + width},${top + r}V${yBase}Z`;
}

/** Money in beside money out, month by month, with what was left written
 * under each pair. Point at a month (or arrow to it) for the three figures. */
export function PnlBars({ months }: { months: PnlMonth[] }) {
  const [ref, { w, h }] = useSize<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const n = months.length;

  const geo = useMemo(() => {
    if (w === 0 || h === 0 || n === 0) return null;
    const top = 12;
    const bottom = 52;
    const yBase = h - bottom;
    const band = w / n;
    const bar = Math.min(24, Math.max(8, band / 5));
    const ticks = niceScale(Math.max(1, ...months.map((m) => Math.max(m.revenue, m.expenses))));
    const yMax = ticks[ticks.length - 1];
    const height = (v: number) => Math.max(0, ((yBase - top) * v) / yMax);
    const centre = (i: number) => band * i + band / 2;
    return { top, yBase, band, bar, ticks, yMax, height, centre };
  }, [w, h, n, months]);

  function onKey(e: React.KeyboardEvent) {
    const at = active ?? n - 1;
    const next = e.key === "ArrowLeft" ? at - 1 : e.key === "ArrowRight" ? at + 1 : null;
    if (e.key === "Escape") setActive(null);
    if (next === null) return;
    e.preventDefault();
    setActive(clamp(next, 0, n - 1));
  }

  const month = active !== null ? months[active] : null;

  return (
    <div
      ref={ref}
      className="chart relative h-full w-full touch-pan-y select-none"
      role="group"
      aria-label="Grafik untung dan rugi per bulan"
      tabIndex={0}
      onKeyDown={onKey}
      onFocus={(e) => e.target === e.currentTarget && e.currentTarget.matches(":focus-visible") && setActive(n - 1)}
      onBlur={() => setActive(null)}
      onPointerMove={(e) => {
        if (!geo || !ref.current) return;
        const px = e.clientX - ref.current.getBoundingClientRect().left;
        setActive(clamp(Math.floor(px / geo.band), 0, n - 1));
      }}
      onPointerLeave={() => setActive(null)}
      onPointerCancel={() => setActive(null)}
    >
      {geo && (
        <svg width={w} height={h} className="absolute inset-0" aria-hidden>
          {geo.ticks.map((t) => {
            const y = geo.yBase - geo.height(t);
            return <line key={t} x1={0} x2={w} y1={y} y2={y} className="chart-grid" />;
          })}
          {active !== null && (
            <rect
              x={geo.centre(active) - Math.min(geo.band, 96) / 2}
              y={geo.top - 6}
              width={Math.min(geo.band, 96)}
              height={geo.yBase - geo.top + 6}
              rx={10}
              className="chart-band"
            />
          )}
          {months.map((m, i) => {
            const cx = geo.centre(i);
            const delay = { animationDelay: `${i * 55}ms` };
            return (
              <g key={m.month} data-dim={active !== null && active !== i} className="chart-month">
                <path
                  d={column(cx - geo.bar - 1, geo.yBase, geo.bar, geo.height(m.revenue))}
                  className="chart-bar chart-bar-in"
                  style={delay}
                />
                <path
                  d={column(cx + 1, geo.yBase, geo.bar, geo.height(m.expenses))}
                  className="chart-bar chart-bar-out"
                  style={{ animationDelay: `${i * 55 + 90}ms` }}
                />
                <text x={cx} y={geo.yBase + 22} textAnchor="middle" className="chart-tick chart-tick-lg">
                  {MONTHS[monthIndex(m.month)] ?? m.month}
                </text>
                <text
                  x={cx}
                  y={geo.yBase + 42}
                  textAnchor="middle"
                  className={`chart-net ${m.net >= 0 ? "chart-net-up" : "chart-net-down"}`}
                >
                  {m.net >= 0 ? "+" : "−"}
                  {compact.format(Math.abs(m.net))}
                </text>
              </g>
            );
          })}
        </svg>
      )}

      {geo && month && active !== null && (
        <div
          className="chart-tip chart-tip-float min-w-[11.5rem]"
          role="status"
          // Beside the month it describes, on whichever side has the room, so
          // it never sits on the bars it is reading out.
          style={{
            transform:
              geo.centre(active) > w / 2
                ? `translate(${geo.centre(active) - geo.bar - 14}px, ${geo.top}px) translate(-100%, 0)`
                : `translate(${geo.centre(active) + geo.bar + 14}px, ${geo.top}px)`,
          }}
        >
          <p className="ink-soft">
            {MONTHS_LONG[monthIndex(month.month)] ?? month.month} {month.month.slice(0, 4)}
          </p>
          <dl className="mt-1 space-y-0.5">
            <div className="flex items-center justify-between gap-4">
              <dt className="ink-soft flex items-center gap-1.5">
                <span className="chart-key chart-key-in" aria-hidden />
                Masuk
              </dt>
              <dd className="font-semibold tabular-nums">{formatRupiah(month.revenue)}</dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="ink-soft flex items-center gap-1.5">
                <span className="chart-key chart-key-out" aria-hidden />
                Keluar
              </dt>
              <dd className="font-semibold tabular-nums">{formatRupiah(month.expenses)}</dd>
            </div>
            <div className="hairline-t mt-1 flex items-center justify-between gap-4 pt-1">
              <dt className="ink-soft">{month.net >= 0 ? "Untung" : "Rugi"}</dt>
              <dd className="text-sm font-semibold tabular-nums">{formatRupiah(Math.abs(month.net))}</dd>
            </div>
          </dl>
        </div>
      )}
    </div>
  );
}

/** The quiet line beside a key figure: which way the last stretch went. */
export function Spark({ values }: { values: number[] }) {
  const W = 96;
  const H = 32;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const xy = values.map(
    (v, i) => [2 + ((W - 4) * i) / (values.length - 1), H - 3 - ((H - 6) * (v - lo)) / span] as [number, number]
  );
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="spark h-full w-full" aria-hidden>
      <path d={monotonePath(xy)} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
