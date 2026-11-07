"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

/** Server action: the redirect happens in the browser, so the visitor stays on the address they opened. */
export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
