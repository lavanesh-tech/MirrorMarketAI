import { z } from "zod";

/*
 * The comparison is stored as an agent run whose `output` is free-form JSON in the
 * API contract, so its shape is checked here before anything is rendered.
 */
const cellSchema = z.object({
  key: z.string(),
  status: z.enum(["MET", "UNMET", "UNKNOWN"]),
  value: z.string().nullable(),
  utility: z.number(),
  contribution: z.number(),
  citations: z.array(z.string()),
});

const productSchema = z.object({
  product_id: z.string(),
  name: z.string(),
  eligible: z.boolean(),
  violations: z.array(z.string()),
  unknown_hard: z.array(z.string()),
  score: z.number(),
  rank: z.number(),
  cells: z.array(cellSchema),
});

const comparisonSchema = z.object({
  criteria: z.array(
    z.object({ key: z.string(), label: z.string(), weight: z.number(), hard: z.boolean() }),
  ),
  products: z.array(productSchema),
  winner_product_id: z.string().nullable(),
  margin: z.number().nullable(),
  sensitivity: z.object({ stable: z.boolean(), winner_changes_with: z.array(z.string()) }),
});

export type Comparison = z.infer<typeof comparisonSchema>;
export type ComparedProduct = z.infer<typeof productSchema>;
export type ComparisonCell = z.infer<typeof cellSchema>;

export function parseComparison(output: unknown): Comparison | null {
  const parsed = comparisonSchema.safeParse(output);
  return parsed.success ? parsed.data : null;
}

export const STATUS_WORDS: Record<ComparisonCell["status"], string> = {
  MET: "Meets",
  UNMET: "Misses",
  UNKNOWN: "Not found",
};

export const MAX_WEIGHT = 10;

/** One line on where a product stands, for the matrix header. */
export function standing(product: ComparedProduct, comparison: Comparison): string {
  const label = (key: string) => comparison.criteria.find((c) => c.key === key)?.label ?? key;
  if (!product.eligible) return `Ruled out: misses ${product.violations.map(label).join(", ")}`;
  if (product.product_id === comparison.winner_product_id) return "Best match";
  if (product.unknown_hard.length > 0) {
    return `Can't confirm: ${product.unknown_hard.map(label).join(", ")}`;
  }
  return "Meets the must-haves";
}

/** "ram_gb x0.5" -> "halve the importance of Memory"; "price x2" -> "double the importance of Price". */
export function weightChanges(comparison: Comparison): string[] {
  return comparison.sensitivity.winner_changes_with.map((entry) => {
    const [key = "", factor = ""] = entry.split(" x");
    const label = comparison.criteria.find((c) => c.key === key)?.label ?? key;
    return `${Number(factor) < 1 ? "halve" : "double"} the importance of ${label}`;
  });
}
