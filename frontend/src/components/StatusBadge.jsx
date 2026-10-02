import { LEVEL_INFO, statusInfo } from "../lib/format.js";

export function StatusBadge({ status }) {
  const info = statusInfo(status);
  return (
    <span className={`badge badge-${info.tone}`} title={info.description}>
      {info.label}
    </span>
  );
}

export function ProofLevelBadge({ level }) {
  return (
    <span className="badge badge-level" title={LEVEL_INFO[level] || ""}>
      {level || "L0"}
    </span>
  );
}
