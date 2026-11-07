/** The command to run on the Pi. `url` is the dashboard's address, without a trailing slash or /api. */
export function pairCommand(url: string, token: string): string {
  return `.venv/bin/argus-cli pair --url ${url.replace(/\/+$/, "")} --token ${token} --config /etc/argus/config.yaml`;
}

export type PairingWarning = "dashboard_loopback" | "supabase_loopback";

/** Reasons the Pi cannot complete pairing or uploads. Signed upload URLs are built from the Supabase URL, so a loopback one breaks every upload even though pairing works. */
export function pairingWarnings(dashboardUrl: string, supabaseUrl: string): PairingWarning[] {
  const out: PairingWarning[] = [];
  if (dashboardUrl && isLoopbackUrl(dashboardUrl)) out.push("dashboard_loopback");
  if (supabaseUrl && isLoopbackUrl(supabaseUrl)) out.push("supabase_loopback");
  return out;
}

/** localhost and loopback addresses only work on the machine running the dashboard, not from the Pi. */
export function isLoopbackUrl(url: string): boolean {
  try {
    const h = new URL(url).hostname;
    return h === "localhost" || h === "127.0.0.1" || h === "::1" || h === "[::1]" || h.endsWith(".localhost");
  } catch {
    return false;
  }
}
