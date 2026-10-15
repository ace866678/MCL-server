import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://mcl-server.vercel.app"),
  title: {
    default: "MCL Server — Minecraft Server Management",
    template: "%s | MCL Server",
  },
  description:
    "Manage your Minecraft server from a clear control panel. Configure server settings, monitor status, and manage your own server environment.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    type: "website",
    url: "https://mcl-server.vercel.app/",
    siteName: "MCL Server",
    title: "MCL Server — Minecraft Server Management",
    description:
      "Manage your Minecraft server from a clear control panel. Configure server settings and monitor your server environment.",
  },
  twitter: {
    card: "summary",
    title: "MCL Server — Minecraft Server Management",
    description:
      "Manage your Minecraft server from a clear control panel and monitor your server environment.",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* Each route renders its own main element. */}
        <div className="page">{children}</div>
        <Analytics />
      </body>
    </html>
  );
}