export default function ErrorMessage({ error }) {
  if (!error) return null;
  const title = error.status === 404 ? "Not found" : error.title || "Something went wrong";
  return (
    <div className="alert" role="alert">
      <strong>{title}.</strong> {error.detail || error.message}
    </div>
  );
}
