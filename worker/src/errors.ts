/** An error retrying cannot fix. */
export class NonRetryableError extends Error {}
export class SpendCapError extends NonRetryableError {}

/** A provider call ran past its time limit. Retrying would only pay for another hang, so it is not retried. */
export class LlmTimeoutError extends NonRetryableError {}

/**
 * The video cannot be analysed (Gemini file went to FAILED, or was still PROCESSING at the limit).
 * Not an event failure: the pipeline falls back to the sensor-only report and records why.
 */
export class VideoUnusableError extends Error {}
