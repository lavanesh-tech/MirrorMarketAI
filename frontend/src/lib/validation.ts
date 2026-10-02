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
