import { NextResponse } from "next/server";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public extra?: Record<string, unknown>,
  ) {
    super(message);
  }
}

export function errorResponse(status: number, code: string, message: string, extra?: Record<string, unknown>) {
  return NextResponse.json({ error: { code, message }, ...extra }, { status });
}

/** Wrap a handler body: ApiError -> JSON error, anything else -> 500 (device retries 5xx). */
export function handleError(e: unknown) {
  if (e instanceof ApiError) return errorResponse(e.status, e.code, e.message, e.extra);
  console.error("[api] unhandled", e);
  return errorResponse(500, "internal", "Internal server error");
}
