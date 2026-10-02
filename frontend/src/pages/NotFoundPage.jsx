import { Link } from "react-router";

export default function NotFoundPage() {
  return (
    <section className="card narrow">
      <h1>Page not found</h1>
      <p>
        <Link to="/projects">Back to projects</Link>
      </p>
    </section>
  );
}
