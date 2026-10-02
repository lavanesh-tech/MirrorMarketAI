"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "@/lib/api/client";
import { usePriceHistory, useRecordPrice, type PriceHistory } from "@/lib/api/queries";
import { formatDate } from "@/lib/format";
import { formatMoney } from "@/lib/requirements";
import { priceSchema } from "@/lib/validation";

const WIDTH = 560;
const HEIGHT = 180;
const PAD = { top: 12, right: 12, bottom: 24, left: 56 };
// Line styles differ by dash as well as colour, so series stay apart without colour.
const STYLES = [
  { stroke: "var(--color-action)", dash: "" },
  { stroke: "var(--color-ink)", dash: "6 4" },
  { stroke: "var(--color-met)", dash: "2 3" },
  { stroke: "var(--color-unmet)", dash: "10 3 2 3" },
];

/** Lowest price per day for each shop. Exported for tests. */
export function chartGeometry(history: PriceHistory) {
  const points = history.series.flatMap((series) =>
    series.points.map((point) => ({ time: Date.parse(point.bucket), low: Number(point.low) })),
  );
  if (points.length === 0) return null;
  const times = points.map((p) => p.time);
  const lows = points.map((p) => p.low);
  const [t0, t1] = [Math.min(...times), Math.max(...times)];
  const margin = (Math.max(...lows) - Math.min(...lows)) * 0.1 || Math.max(...lows) * 0.05 || 1;
  const [y0, y1] = [Math.min(...lows) - margin, Math.max(...lows) + margin];
  const x = (time: number) =>
    PAD.left + (t1 === t0 ? 0.5 : (time - t0) / (t1 - t0)) * (WIDTH - PAD.left - PAD.right);
  const y = (low: number) =>
    PAD.top + (1 - (low - y0) / (y1 - y0)) * (HEIGHT - PAD.top - PAD.bottom);
  return {
    range: { from: t0, to: t1, low: y0, high: y1 },
    lines: history.series.map((series) => ({
      retailer: series.retailer,
      points: series.points.map((point) => ({
        x: x(Date.parse(point.bucket)),
        y: y(Number(point.low)),
      })),
    })),
  };
}

export function PriceHistoryView({
  productId,
  productName,
  canRecord,
}: {
  productId: string;
  productName: string;
  canRecord: boolean;
}) {
  const history = usePriceHistory(productId);
  const record = useRecordPrice(productId);
  const [error, setError] = useState<string | null>(null);
  const id = `price-${productId}`;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const parsed = priceSchema.safeParse(Object.fromEntries(new FormData(form)));
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Check the price.");
      return;
    }
    setError(null);
    try {
      await record.mutateAsync({ ...parsed.data, observedAt: new Date().toISOString() });
      form.reset();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "The price could not be saved.");
    }
  }

  if (history.isPending) return <p className="text-muted">Loading prices…</p>;
  if (history.isError) return <p role="alert">The prices could not be loaded.</p>;

  const { stats, currency } = history.data;
  const geometry = chartGeometry(history.data);
  const money = (amount: string | null) => (amount === null ? "—" : formatMoney(amount, currency));

  return (
    <div className="space-y-4">
      {geometry && stats.lowest_current ? (
        <>
          <dl className="grid max-w-2xl grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
            <div>
              <dt className="text-sm text-muted">Lowest now</dt>
              <dd className="font-display text-xl font-semibold tabular-nums">
                {money(stats.lowest_current.amount)}
              </dd>
              <dd className="text-sm text-muted">{stats.lowest_current.retailer}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Lowest ever</dt>
              <dd className="font-semibold tabular-nums">{money(stats.all_time_low)}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">Highest ever</dt>
              <dd className="font-semibold tabular-nums">{money(stats.all_time_high)}</dd>
            </div>
            <div>
              <dt className="text-sm text-muted">{stats.window_days}-day average</dt>
              <dd className="font-semibold tabular-nums">{money(stats.window_average)}</dd>
            </div>
          </dl>
          <figure className="max-w-2xl">
            <svg
              viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
              role="img"
              aria-label={`Price of ${productName} over time, lowest per day, from ${formatDate(
                new Date(geometry.range.from).toISOString(),
              )} to ${formatDate(new Date(geometry.range.to).toISOString())}`}
              className="w-full border border-line bg-surface"
            >
              {[
                geometry.range.high,
                (geometry.range.high + geometry.range.low) / 2,
                geometry.range.low,
              ].map((value, index) => {
                const y = PAD.top + (index / 2) * (HEIGHT - PAD.top - PAD.bottom);
                return (
                  <g key={index}>
                    <line
                      x1={PAD.left}
                      x2={WIDTH - PAD.right}
                      y1={y}
                      y2={y}
                      stroke="var(--color-line)"
                    />
                    <text
                      x={PAD.left - 6}
                      y={y + 4}
                      textAnchor="end"
                      fontSize="11"
                      fill="var(--color-muted)"
                    >
                      {formatMoney(Math.round(value), currency)}
                    </text>
                  </g>
                );
              })}
              <text x={PAD.left} y={HEIGHT - 6} fontSize="11" fill="var(--color-muted)">
                {formatDate(new Date(geometry.range.from).toISOString())}
              </text>
              <text
                x={WIDTH - PAD.right}
                y={HEIGHT - 6}
                fontSize="11"
                textAnchor="end"
                fill="var(--color-muted)"
              >
                {formatDate(new Date(geometry.range.to).toISOString())}
              </text>
              {geometry.lines.map((line, index) => {
                const style = STYLES[index % STYLES.length]!;
                return (
                  <g key={line.retailer}>
                    <polyline
                      fill="none"
                      stroke={style.stroke}
                      strokeWidth="2"
                      strokeDasharray={style.dash}
                      points={line.points
                        .map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`)
                        .join(" ")}
                    />
                    {line.points.map((p, i) => (
                      <circle key={i} cx={p.x} cy={p.y} r="2.5" fill={style.stroke} />
                    ))}
                  </g>
                );
              })}
            </svg>
            <figcaption className="mt-2">
              <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
                {geometry.lines.map((line, index) => {
                  const style = STYLES[index % STYLES.length]!;
                  const latest = stats.current.find((price) => price.retailer === line.retailer);
                  return (
                    <li key={line.retailer} className="flex items-center gap-1.5">
                      <svg width="24" height="8" aria-hidden="true">
                        <line
                          x1="0"
                          x2="24"
                          y1="4"
                          y2="4"
                          stroke={style.stroke}
                          strokeWidth="2"
                          strokeDasharray={style.dash}
                        />
                      </svg>
                      {line.retailer}
                      {latest ? (
                        <>
                          : <span className="font-semibold">{money(latest.amount)}</span>
                          <span className="text-muted">on {formatDate(latest.observed_at)}</span>
                        </>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </figcaption>
          </figure>
        </>
      ) : (
        <p className="max-w-xl text-muted">
          No prices recorded yet. Recorded prices are used for the budget check and the value score.
        </p>
      )}
      {canRecord ? (
        <form onSubmit={onSubmit} noValidate className="max-w-2xl">
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr_1fr_auto] sm:items-end">
            <div>
              <label htmlFor={`${id}-retailer`} className="block text-sm font-semibold">
                Shop
              </label>
              <input id={`${id}-retailer`} name="retailer" className="field" />
            </div>
            <div>
              <label htmlFor={`${id}-amount`} className="block text-sm font-semibold">
                Price today
              </label>
              <input id={`${id}-amount`} name="amount" inputMode="decimal" className="field" />
            </div>
            <div>
              <label htmlFor={`${id}-currency`} className="block text-sm font-semibold">
                Currency
              </label>
              <input
                id={`${id}-currency`}
                name="currency"
                className="field"
                defaultValue={currency || "USD"}
                maxLength={3}
              />
            </div>
            <button type="submit" className="button-quiet" disabled={record.isPending}>
              {record.isPending ? "Saving…" : "Record price"}
            </button>
          </div>
          {error ? (
            <p role="alert" className="mt-2 text-sm font-medium text-unmet">
              {error}
            </p>
          ) : null}
        </form>
      ) : null}
    </div>
  );
}
