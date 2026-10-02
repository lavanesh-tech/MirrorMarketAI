import type { Schemas } from "./api/client";

export type RequirementSpec = Schemas["RequirementSpec-Output"];
export type Criterion = Schemas["Criterion-Output"];
export type Budget = Schemas["Budget-Output"];
export type Role = Schemas["WorkspaceRole"];

// The same labels the API uses in the comparison matrix, so both screens agree.
const LABELS: Record<string, string> = {
  ram_gb: "Memory",
  storage_gb: "Storage",
  battery_life_hours: "Battery life",
  weight_kg: "Weight",
  screen_size_in: "Screen size",
  refresh_rate_hz: "Refresh rate",
  brightness_nits: "Brightness",
  battery_wh: "Battery capacity",
  battery_mah: "Battery capacity",
  price: "Price",
};

/** "ram_gb" -> "Memory", "has_usb_c" -> "Usb c". */
export function criterionLabel(key: string): string {
  const known = LABELS[key];
  if (known) return known;
  const words = key.replace(/^(has|is)_/, "").replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const OPERATOR_WORDS: Record<Criterion["operator"], string> = {
  ">=": "at least",
  "<=": "at most",
  "=": "exactly",
  "!=": "not",
};

/** The target of a criterion in words: "at least 16 GB", "Yes", "not Dell". */
export function criterionTarget(criterion: Criterion): string {
  if (criterion.value_number != null) {
    const amount = trimNumber(criterion.value_number);
    return `${OPERATOR_WORDS[criterion.operator]} ${amount}${criterion.unit ? ` ${criterion.unit}` : ""}`;
  }
  const text = criterion.value_text ?? "";
  const lower = text.toLowerCase();
  if (lower === "yes" || lower === "no") {
    const wanted = (lower === "yes") === (criterion.operator === "=");
    return wanted ? "Yes" : "No";
  }
  return criterion.operator === "=" ? text : `not ${text}`;
}

/** "16.000000" -> "16", "1.400000" -> "1.4". Other text is returned unchanged. */
export function trimNumber(value: string | number): string {
  const text = String(value);
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text;
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

export function formatMoney(amount: string | number, currency: string): string {
  const value = Number(amount);
  if (!Number.isFinite(value)) return `${amount} ${currency}`;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
    }).format(value);
  } catch {
    return `${trimNumber(amount)} ${currency}`;
  }
}

export function budgetText(budget: Budget): string {
  const { min_amount: min, max_amount: max, currency } = budget;
  if (min != null && max != null) {
    return `${formatMoney(min, currency)} to ${formatMoney(max, currency)}`;
  }
  if (max != null) return `Up to ${formatMoney(max, currency)}`;
  if (min != null) return `At least ${formatMoney(min, currency)}`;
  return "";
}

/** OWNER and EDITOR change requirements and products; the API enforces the same rule. */
export function canEdit(role: Role): boolean {
  return role === "OWNER" || role === "EDITOR";
}

/** Everyone except VIEWER may run research and comparisons. */
export function canRun(role: Role): boolean {
  return role !== "VIEWER";
}
