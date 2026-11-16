import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/atkinson-hyperlegible-next";
import "./globals.css";

import type { Metadata } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: { default: "MirrorMarket", template: "%s | MirrorMarket" },
  description: "Compare products together, with a source behind every claim.",
};

// Every page is rendered per request: a page built ahead of time would carry no
// nonce, and the Content-Security-Policy would then block its scripts.
export default async function RootLayout({ children }: { children: ReactNode }) {
  await connection();
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
