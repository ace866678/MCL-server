import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";

export async function POST(request: Request) {
  await (await createClient()).auth.signOut();
  return NextResponse.redirect(new URL("/", request.url), 303);
}
