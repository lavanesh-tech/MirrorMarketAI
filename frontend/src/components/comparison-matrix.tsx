import { STATUS_WORDS, standing, type Comparison, type ComparisonCell } from "@/lib/comparison";
import { formatMoney, trimNumber } from "@/lib/requirements";

type Props = {
  comparison: Comparison;
  /** Units from the requirements, by criterion key (the comparison stores bare values). */
  units: Record<string, string>;
  currency: string;
};

const STATUS_STYLE: Record<ComparisonCell["status"], string> = {
  MET: "text-met",
  UNMET: "text-unmet",
  UNKNOWN: "text-muted",
};

/** Criteria down the side, products across the top, best match first. */
export function ComparisonMatrix({ comparison, units, currency }: Props) {
  function shown(cell: ComparisonCell): string | null {
    if (cell.value === null) return null;
    if (cell.key === "price") return formatMoney(cell.value, currency);
    const unit = units[cell.key];
    return `${trimNumber(cell.value)}${unit ? ` ${unit}` : ""}`;
  }

  return (
    <div
      role="region"
      aria-label="Comparison matrix"
      tabIndex={0}
      className="overflow-x-auto border-y-2 border-ink bg-surface"
    >
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">
          Each product scored against each requirement. Products are ordered best match first.
        </caption>
        <thead>
          <tr className="border-b-2 border-ink align-bottom">
            <th scope="col" className="min-w-40 px-4 py-3 text-sm font-semibold">
              Requirement
            </th>
            {comparison.products.map((product) => {
              const winner = product.product_id === comparison.winner_product_id;
              return (
                <th key={product.product_id} scope="col" className="min-w-44 px-4 py-3">
                  <span className="block text-sm font-normal text-muted">#{product.rank}</span>
                  <span className={`font-display text-lg font-semibold ${winner ? "mark" : ""}`}>
                    {product.name}
                  </span>
                  <span className="mt-1 block font-display text-2xl font-semibold tabular-nums">
                    {product.score.toFixed(1)}
                    <span className="text-sm font-normal text-muted"> of 100</span>
                  </span>
                  <span
                    className={`mt-1 block text-sm font-semibold ${
                      product.eligible ? "" : "text-unmet"
                    }`}
                  >
                    {standing(product, comparison)}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {comparison.criteria.map((criterion) => (
            <tr key={criterion.key} className="border-b border-line align-top">
              <th scope="row" className="px-4 py-3 font-semibold">
                {criterion.label}
                {criterion.hard ? <span className="tag mt-1 block w-fit">Must have</span> : null}
                <span className="mt-1 block text-sm font-normal text-muted">
                  Importance {trimNumber(criterion.weight)}
                </span>
              </th>
              {comparison.products.map((product) => {
                const cell = product.cells.find((c) => c.key === criterion.key);
                if (!cell) return <td key={product.product_id} className="px-4 py-3" />;
                const value = shown(cell);
                return (
                  <td key={product.product_id} className="px-4 py-3">
                    {value ? (
                      <span
                        className={`font-semibold tabular-nums ${cell.citations.length > 0 ? "mark" : ""}`}
                      >
                        {value}
                      </span>
                    ) : null}
                    <span className={`block text-sm font-semibold ${STATUS_STYLE[cell.status]}`}>
                      {STATUS_WORDS[cell.status]}
                    </span>
                    <span className="block text-sm text-muted tabular-nums">
                      +{cell.contribution.toFixed(1)} points
                    </span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
