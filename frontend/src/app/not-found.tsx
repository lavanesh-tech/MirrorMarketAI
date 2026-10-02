import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-xl px-6 py-24">
      <h1 className="text-title">This page does not exist</h1>
      <p className="mt-3 text-muted">The address may be mistyped, or the page was moved.</p>
      <Link href="/workspaces" className="link mt-6 inline-block">
        Go to your workspaces
      </Link>
    </main>
  );
}
