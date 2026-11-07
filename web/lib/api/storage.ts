export const MEDIA_BUCKET = "event-media";
/** Supabase signed upload URLs are valid for 2 hours. */
export const SIGNED_UPLOAD_TTL_S = 2 * 60 * 60;

/** Filenames are device-supplied: no separators, no dot-segments, bounded length. */
export function isSafeFilename(name: string): boolean {
  return /^[A-Za-z0-9._-]{1,200}$/.test(name) && name !== "." && name !== "..";
}

/** Storage object path: `<org_id>/<event_id>/<filename>` (RLS read policy keys on the first folder). */
export function mediaPath(orgId: string, eventId: string, filename: string): string {
  if (!isSafeFilename(filename)) throw new Error(`unsafe filename: ${filename}`);
  return `${orgId}/${eventId}/${filename}`;
}
