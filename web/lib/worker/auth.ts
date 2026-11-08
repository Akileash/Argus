import { createHash, timingSafeEqual } from "node:crypto";

/** Constant-time check of `Authorization: Bearer <secret>`. Fails closed when the secret is not set. */
export function isAuthorized(request: Request, secret: string | undefined = process.env.WORKER_CRON_SECRET): boolean {
  if (!secret) return false;
  const m = /^Bearer (.+)$/.exec(request.headers.get("authorization") ?? "");
  // Hash both sides: timingSafeEqual needs equal lengths, and hashing hides the length of the real secret.
  const a = createHash("sha256").update(m?.[1] ?? "").digest();
  const b = createHash("sha256").update(secret).digest();
  return timingSafeEqual(a, b) && m !== null;
}
