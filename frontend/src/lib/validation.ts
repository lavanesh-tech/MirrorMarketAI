import { z } from "zod";

// The same limits the API enforces, so most mistakes are caught before a round trip.
export const PASSWORD_MIN_LENGTH = 12;

export const loginSchema = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
});

export const registerSchema = z.object({
  display_name: z
    .string()
    .trim()
    .min(1, "Enter your name.")
    .max(100, "Use 100 characters or fewer."),
  email: z.email("Enter a valid email address."),
  password: z
    .string()
    .min(PASSWORD_MIN_LENGTH, `Use at least ${PASSWORD_MIN_LENGTH} characters.`)
    .max(128, "Use 128 characters or fewer."),
});

export const workspaceSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Give the workspace a name.")
    .max(120, "Use 120 characters or fewer."),
});

export type FieldErrors<T> = Partial<Record<keyof T, string>>;

/** First message per field, in a shape a form can render directly. */
export function fieldErrors<T>(error: z.ZodError<T>): FieldErrors<T> {
  const result: FieldErrors<T> = {};
  for (const issue of error.issues) {
    const field = issue.path[0] as keyof T | undefined;
    if (field !== undefined && result[field] === undefined) result[field] = issue.message;
  }
  return result;
}

/** Only allow redirects to our own pages after login (never to another site). */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return "/workspaces";
  }
  return next;
}

export const CATEGORIES = [
  "laptop",
  "desktop",
  "monitor",
  "phone",
  "tablet",
  "headphones",
  "camera",
  "appliance",
  "other",
] as const;

export const productSchema = z.object({
  brand: z.string().trim().min(1, "Enter the brand.").max(100, "Use 100 characters or fewer."),
  name: z
    .string()
    .trim()
    .min(1, "Enter the product name.")
    .max(200, "Use 200 characters or fewer."),
  category: z.enum(CATEGORIES, "Choose a category."),
});

const NUMBER = /^-?\d{1,12}(\.\d{1,6})?$/;

/** One specification row. A value that reads as a number is stored as a number. */
export const specificationSchema = z
  .object({
    key: z
      .string()
      .trim()
      .regex(
        /^[a-z][a-z0-9_]{0,63}$/,
        "Use lowercase letters, digits and underscores, like ram_gb.",
      ),
    value: z.string().trim().min(1, "Enter a value.").max(500, "Use 500 characters or fewer."),
    unit: z.string().trim().max(16, "Use 16 characters or fewer."),
  })
  .transform(({ key, value, unit }) => ({
    key,
    unit: unit === "" ? null : unit,
    ...(NUMBER.test(value) ? { value_number: value } : { value_text: value }),
  }));

export const briefSchema = z.object({
  text: z
    .string()
    .trim()
    .min(1, "Describe what you need first.")
    .max(4000, "Use 4,000 characters or fewer."),
});

export const SOURCE_TYPES = [
  ["SPECIFICATION_SHEET", "Specification sheet"],
  ["MANUFACTURER_PAGE", "Manufacturer page"],
  ["REVIEW", "Review"],
  ["MANUAL", "Manual"],
  ["WARRANTY", "Warranty"],
  ["RETURN_POLICY", "Return policy"],
  ["USER_DOCUMENT", "My own notes"],
  ["OTHER", "Other"],
] as const;

const sourceType = z.enum(
  SOURCE_TYPES.map(([value]) => value) as [
    (typeof SOURCE_TYPES)[number][0],
    ...(typeof SOURCE_TYPES)[number][0][],
  ],
  "Choose what kind of source this is.",
);
const sourceTitle = z
  .string()
  .trim()
  .min(1, "Give the source a title.")
  .max(300, "Use 300 characters or fewer.");

export const textSourceSchema = z.object({
  title: sourceTitle,
  sourceType,
  text: z
    .string()
    .trim()
    .min(20, "Paste at least a sentence or two.")
    .max(200_000, "That is too long to paste; upload it as a file instead."),
});

export const urlSourceSchema = z.object({
  title: sourceTitle,
  sourceType,
  url: z.url({ protocol: /^https?$/, error: "Enter a web address starting with https://." }),
});

export const priceSchema = z.object({
  retailer: z.string().trim().min(1, "Enter the shop.").max(100, "Use 100 characters or fewer."),
  amount: z
    .string()
    .trim()
    .regex(/^\d{1,10}(\.\d{1,2})?$/, "Enter a price like 1299 or 1299.99.")
    .refine((value) => Number(value) > 0, "Enter a price above zero."),
  currency: z
    .string()
    .trim()
    .toUpperCase()
    .regex(/^[A-Z]{3}$/, "Use a three-letter currency code, like USD."),
});

export const commentSchema = z.object({
  body: z
    .string()
    .trim()
    .min(1, "Write something first.")
    .max(4000, "Use 4,000 characters or fewer."),
});

export const questionSchema = z.object({
  question: z
    .string()
    .trim()
    .min(3, "Ask a question first.")
    .max(500, "Use 500 characters or fewer."),
});
