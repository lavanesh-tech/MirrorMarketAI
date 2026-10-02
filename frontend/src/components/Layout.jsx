import { Link, NavLink, Outlet } from "react-router";
import { useAuth } from "../auth/AuthContext.jsx";

export default function Layout() {
  const { isAuthenticated, email, logout } = useAuth();
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          Proof<span>Stack</span>
        </Link>
        <nav aria-label="Main">
          {isAuthenticated ? (
            <>
              <NavLink to="/projects">Projects</NavLink>
              <span className="muted small">{email}</span>
              <button type="button" className="link-button" onClick={logout}>
                Sign out
              </button>
            </>
          ) : (
            <NavLink to="/login">Sign in</NavLink>
          )}
        </nav>
      </header>
      <main className="container">
        <Outlet />
      </main>
      <footer className="footer muted small">
        Statuses and Proof Levels are computed by a deterministic evaluator, never by an AI model.
      </footer>
    </div>
  );
}
