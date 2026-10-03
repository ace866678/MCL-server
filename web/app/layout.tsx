import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MCL Control Plane",
  description: "Securely manage Minecraft servers running on your own Windows or Linux device.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* A div, not a <main>: each route renders its own <main>, and a
            document may only have one. `.page` carries the same layout. */}
        <div className="page">{children}</div>
      </body>
    </html>
  );
}
