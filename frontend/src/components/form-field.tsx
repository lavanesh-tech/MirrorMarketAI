import type { InputHTMLAttributes } from "react";

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  name: string;
  error?: string;
  hint?: string;
};

/** A labelled input whose error and hint are announced by screen readers. */
export function FormField({ label, name, error, hint, ...input }: Props) {
  const describedBy = [hint ? `${name}-hint` : null, error ? `${name}-error` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="space-y-1.5">
      <label htmlFor={name} className="block font-semibold">
        {label}
      </label>
      {hint ? (
        <p id={`${name}-hint`} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
      <input
        id={name}
        name={name}
        className="field"
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        {...input}
      />
      {error ? (
        <p id={`${name}-error`} className="text-sm font-medium text-unmet">
          {error}
        </p>
      ) : null}
    </div>
  );
}
