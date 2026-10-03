import Link from "next/link";
import { redirect } from "next/navigation";
import { supabaseConfigured } from "@/lib/supabase/env";
import { createClient } from "@/lib/supabase/server";
import { NewServerForm, type AgentOption } from "./new-server-form";

export const dynamic = "force-dynamic";

export default async function NewServerPage() {
  if (!supabaseConfigured()) redirect("/");

  const supabase = await createClient();
  const { data } = await supabase
    .from("agents")
    .select("id,name,platform")
    .order("created_at", { ascending: false });

  const agents: AgentOption[] = Array.isArray(data) ? data : [];

  return (
    <main>
      <section className="section-heading">
        <div>
          <p className="eyebrow">NEW SERVER</p>
          <h2>Create a server</h2>
        </div>
        <Link className="button-link" href="/">
          Cancel
        </Link>
      </section>
      <NewServerForm agents={agents} />
    </main>
  );
}