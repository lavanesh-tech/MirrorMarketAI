import { redirect } from "next/navigation";

// Signed-out visitors never get here: src/proxy.ts sends them to /login first.
export default function Home() {
  redirect("/workspaces");
}
