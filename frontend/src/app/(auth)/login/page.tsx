import type { Metadata } from "next";
import { Suspense } from "react";

import { AuthForm } from "@/components/auth-form";

export const metadata: Metadata = { title: "Log in" };

export default function LoginPage() {
  // Suspense: the form reads ?next= from the URL, which is only known in the browser.
  return (
    <Suspense>
      <AuthForm mode="login" />
    </Suspense>
  );
}
