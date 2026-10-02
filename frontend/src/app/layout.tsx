import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/atkinson-hyperlegible-next";
import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: { default: "MirrorMarket", template: "%s | MirrorMarket" },
  description: "Compare products together, with a source behind every claim.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
