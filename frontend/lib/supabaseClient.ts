import { createClient, SupabaseClient } from "@supabase/supabase-js";

// Auth optionnelle : si les variables ne sont pas définies, `supabase` vaut null
// et le dashboard retombe sur l'ancien flux (sans login). Aucun crash au build.
const url = process.env.NEXT_PUBLIC_SUPABASE_URL || "";
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "";

export const supabase: SupabaseClient | null =
  url && key ? createClient(url, key) : null;
