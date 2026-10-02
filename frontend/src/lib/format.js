export const STATUS_INFO = {
  SUPPORTED: {
    label: "Supported",
    tone: "good",
    description: "The recorded evidence meets every condition of the claim's contract.",
  },
  PARTIALLY_SUPPORTED: {
    label: "Partially supported",
    tone: "warn",
    description: "Evidence was measured but only part of the claim is met.",
  },
  NOT_VERIFIED: {
    label: "Not verified",
    tone: "neutral",
    description: "There is no evidence yet, or the evidence does not support the claim.",
  },
  CHECK_FAILED: {
    label: "Check failed",
    tone: "bad",
    description: "The verification could not run to completion (build error, timeout, crash).",
  },
  UNSUPPORTED: {
    label: "Unsupported",
    tone: "muted",
    description: "ProofStack has no verification capability for this claim yet.",
  },
};

export const LEVEL_INFO = {
  L0: "Claim only: nothing has been checked.",
  L1: "A verification plan exists for this repository.",
  L2: "The target check was executed.",
  L3: "A quantitative metric was measured.",
  L4: "Repeated measurements were consistent (low variation).",
};

export function statusInfo(status) {
  return STATUS_INFO[status] || { label: status || "Unknown", tone: "muted", description: "" };
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function shortSha(sha) {
  return sha ? String(sha).slice(0, 12) : "—";
}

export function humanize(value) {
  return String(value || "")
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}
