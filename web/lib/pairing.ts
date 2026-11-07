/** The command to run on the Pi. `url` is the dashboard's address, without a trailing slash or /api. */
export function pairCommand(url: string, token: string): string {
  return `.venv/bin/argus-cli pair --url ${url.replace(/\/+$/, "")} --token ${token} --config /etc/argus/config.yaml`;
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
