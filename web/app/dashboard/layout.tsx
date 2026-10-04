import Link from "next/link";
import { redirect } from "next/navigation";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  // Layouts and pages render concurrently, so the pages guard this too; this is
  // the first thing to run and sends an unprovisioned deployment home.
  if (!supabaseConfigured()) redirect("/");

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/");

  return (
    <main>
      <header className="topbar">
        <div>
          <p className="eyebrow">MCL CONTROL PLANE</p>
          <h1>
            <Link href="/" style={{ color: "inherit", textDecoration: "none" }}>
              Dashboard
            </Link>
          </h1>
        </div>
        <form action="/auth/signout" method="post">
          <button className="secondary">Sign out</button>
        </form>
      </header>
      {children}
    </main>
  );
}