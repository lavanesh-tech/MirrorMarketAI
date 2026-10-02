import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/endpoints.js";
import { useAuth } from "../auth/AuthContext.jsx";
import ErrorMessage from "../components/ErrorMessage.jsx";
import Loading from "../components/Loading.jsx";
import { useApi } from "../hooks/useApi.js";

export default function ProjectsPage() {
  const { token } = useAuth();
  const projects = useApi(() => api.listProjects(token), [token]);
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState("private");
  const [createError, setCreateError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function onCreate(event) {
    event.preventDefault();
    setBusy(true);
    setCreateError(null);
    try {
      await api.createProject(token, { name: name.trim(), visibility });
      setName("");
      projects.reload();
    } catch (caught) {
      setCreateError(caught);
    } finally {
      setBusy(false);
    }
  }

  const items = projects.data?.items || [];
  return (
    <>
      <h1>Projects</h1>
      <section className="card">
        <h2>New project</h2>
        <form className="form inline" onSubmit={onCreate}>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
          </label>
          <label>
            Visibility
            <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
              <option value="private">Private</option>
              <option value="public">Public</option>
            </select>
          </label>
          <button type="submit" className="primary" disabled={busy || !name.trim()}>
            Create
          </button>
        </form>
        <ErrorMessage error={createError} />
      </section>
      <section>
        {projects.loading && <Loading />}
        <ErrorMessage error={projects.error} />
        {!projects.loading && !projects.error && items.length === 0 && (
          <p className="muted">No projects yet. Create one to start adding claims.</p>
        )}
        <ul className="list">
          {items.map((project) => (
            <li key={project.id} className="list-item">
              <Link to={`/projects/${project.id}`}>{project.name}</Link>
              <span className={`tag tag-${project.visibility}`}>{project.visibility}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
