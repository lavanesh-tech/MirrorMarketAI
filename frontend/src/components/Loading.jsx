export default function Loading({ label = "Loading…" }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {label}
    </p>
  );
}
