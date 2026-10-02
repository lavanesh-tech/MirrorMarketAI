import { Link, useParams } from "react-router";
import { api } from "../api/endpoints.js";
import { useAuth } from "../auth/AuthContext.jsx";
import ErrorMessage from "../components/ErrorMessage.jsx";
import Loading from "../components/Loading.jsx";
import { ProofLevelBadge, StatusBadge } from "../components/StatusBadge.jsx";
import { useApi } from "../hooks/useApi.js";
import { formatDate, humanize } from "../lib/format.js";

export default function ProjectPage() {
  const { projectId } = useParams();
  const { token } = useAuth();
  const project = useApi(() => api.getProject(token, projectId), [token, projectId]);
  const claims = useApi(() => api.listClaims(token, projectId), [token, projectId]);

  if (project.loading) return <Loading />;
  if (project.error) return <ErrorMessage error={project.error} />;
  const items = claims.data?.items || [];

  return (
    <>
      <p className="breadcrumbs">
        <Link to="/projects">Projects</Link> / {project.data.name}
      </p>
      <h1>
        {project.data.name}{" "}
        <span className={`tag tag-${project.data.visibility}`}>{project.data.visibility}</span>
      </h1>
      <h2>Claims</h2>
      {claims.loading && <Loading />}
      <ErrorMessage error={claims.error} />
      {!claims.loading && !claims.error && items.length === 0 && (
        <p className="muted">No claims yet.</p>
      )}
      {items.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Claim</th>
              <th scope="col">Type</th>
              <th scope="col">Status</th>
              <th scope="col">Proof level</th>
              <th scope="col">Evaluated</th>
            </tr>
          </thead>
          <tbody>
            {items.map((claim) => (
              <tr key={claim.id}>
                <td>
                  <Link to={`/claims/${claim.id}`}>{claim.statement}</Link>
                </td>
                <td className="small">{humanize(claim.spec?.type)}</td>
                <td>
                  <StatusBadge status={claim.status} />
                </td>
                <td>
                  <ProofLevelBadge level={claim.proof_level} />
                </td>
                <td className="small">{formatDate(claim.evaluated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
