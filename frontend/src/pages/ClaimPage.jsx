import { Link, useParams } from "react-router";
import { api } from "../api/endpoints.js";
import { useAuth } from "../auth/AuthContext.jsx";
import ErrorMessage from "../components/ErrorMessage.jsx";
import Loading from "../components/Loading.jsx";
import { ProofLevelBadge, StatusBadge } from "../components/StatusBadge.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate, humanize, shortSha } from "../lib/format.js";

const EVENT_LABELS = {
  claim_created: "Claim created",
  contract_compiled: "Evidence contract compiled",
  plan_created: "Verification plan created",
  run_queued: "Run queued",
  run_started: "Run started",
  run_finished: "Run finished",
  evidence_sealed: "Evidence package sealed",
  evaluated: "Evaluated",
};

function EventDetails({ event }) {
  const d = event.details || {};
  if (event.kind === "evaluated") {
    return (
      <span>
        <StatusBadge status={d.status} /> <ProofLevelBadge level={d.proof_level} />
        {d.changed && d.previous_status && (
          <span className="small muted"> (was {humanize(d.previous_status)})</span>
        )}
      </span>
    );
  }
  if (event.kind === "run_finished") return <span className="small">{humanize(d.status)}</span>;
  if (event.kind === "plan_created") {
    return (
      <span className="small">
        {d.status}
        {d.unsupported_reason ? ` — ${humanize(d.unsupported_reason)}` : ""}
      </span>
    );
  }
  if (event.kind === "evidence_sealed") {
    return <span className="small mono">commit {shortSha(d.commit_sha)}</span>;
  }
  return null;
}

export default function ClaimPage() {
  const { claimId } = useParams();
  const { token } = useAuth();
  const claim = useApi(() => api.getClaim(token, claimId), [token, claimId]);
  const history = useApi(
    () =>
      Promise.all([
        api.getTimeline(token, claimId),
        api.listEvaluations(token, claimId),
        api.listEvidencePackages(token, claimId),
      ]),
    [token, claimId],
  );

  if (claim.loading) return <Loading />;
  if (claim.error) return <ErrorMessage error={claim.error} />;
  const data = claim.data;
  const [timeline, evaluations, packages] = history.data || [{ events: [] }, [], []];
  const latest = evaluations[0];

  return (
    <>
      <p className="breadcrumbs">
        <Link to={`/projects/${data.project_id}`}>Project</Link> / Claim
      </p>
      <h1>{data.statement}</h1>
      <p className="claim-summary">
        <StatusBadge status={data.status} /> <ProofLevelBadge level={data.proof_level} />
        <span className="small muted"> {humanize(data.spec?.type)}</span>
      </p>

      {history.loading && <Loading />}
      <ErrorMessage error={history.error} />

      {latest && (
        <section className="card">
          <h2>Why this status</h2>
          <ul>
            {latest.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          <p className="small muted">
            Commit <span className="mono">{shortSha(latest.commit_sha)}</span> · evaluated{" "}
            {formatDate(latest.created_at)} · evaluator v{latest.evaluator_version}
          </p>
        </section>
      )}

      {history.data && (
        <>
          <section>
            <h2>Evidence packages</h2>
            {packages.length === 0 ? (
              <p className="muted">No evidence has been recorded yet.</p>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Sealed</th>
                    <th scope="col">Commit</th>
                    <th scope="col">SHA-256</th>
                    <th scope="col">Integrity</th>
                  </tr>
                </thead>
                <tbody>
                  {packages.map((p) => (
                    <tr key={p.id}>
                      <td className="small">{formatDate(p.created_at)}</td>
                      <td className="mono small">{shortSha(p.commit_sha)}</td>
                      <td className="mono small" title={p.content_hash}>
                        {shortSha(p.content_hash)}
                      </td>
                      <td>
                        {p.integrity_verified ? (
                          <span className="badge badge-good">verified</span>
                        ) : (
                          <span className="badge badge-bad">hash mismatch</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
          <section>
            <h2>Timeline</h2>
            <ol className="timeline" aria-label="Timeline">
              {timeline.events.map((event) => (
                <li key={`${event.kind}-${event.ref_id}-${event.at}`}>
                  <time dateTime={event.at}>{formatDate(event.at)}</time>
                  <strong>{EVENT_LABELS[event.kind] || humanize(event.kind)}</strong>
                  <EventDetails event={event} />
                </li>
              ))}
            </ol>
          </section>
        </>
      )}
    </>
  );
}
