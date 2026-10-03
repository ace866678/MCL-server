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
        <main className="page">{children}</main>
      </body>
    </html>
  );
}
