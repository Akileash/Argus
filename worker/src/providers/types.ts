/** Thin provider boundaries. Real implementations wrap the official SDKs; tests inject mocks. */

export interface ProviderResult {
  /** Raw JSON text returned by the model. */
  text: string;
  inputTokens: number;
  /** Includes thinking tokens, which bill as output. */
  outputTokens: number;
  /** Model that actually served the request (Claude may fall back server-side). */
  model: string;
}

/** Time limits the pipeline passes to a provider call. */
export interface CallTimeouts {
  /** Limit for each single network step (upload, generate, Claude request). */
  callMs: number;
  /** How long to wait for a Gemini file to leave PROCESSING. */
  processingMs: number;
  /** Retry policy for transient Gemini errors (429/5xx/UNAVAILABLE/network). Default: 1 try, i.e. none. */
  transient?: { tries: number; baseMs: number };
  /** Called before each transient retry sleep (the pipeline logs and refreshes the heartbeat). */
  onTransientRetry?: (info: { attempt: number; tries: number; delayMs: number; error: unknown }) => void;
}

export interface GeminiClient {
  /**
   * Upload the MP4 (Files API), wait until ACTIVE (at most `timeouts.processingMs`), then generate JSON constrained
   * by `schema`. Throws VideoUnusableError when the file goes FAILED or stays PROCESSING, LlmTimeoutError on a hung
   * call. Always deletes the uploaded file afterwards.
   */
  generateWithVideo(args: {
    model: string;
    systemInstruction: string;
    prompt: string;
    video: Uint8Array;
    mimeType: string;
    displayName: string;
    schema: Record<string, unknown>;
    timeouts?: CallTimeouts;
  }): Promise<ProviderResult>;
}

export interface ClaudeClient {
  generateJson(args: {
    model: string;
    system: string;
    prompt: string;
    schema: Record<string, unknown>;
    timeouts?: CallTimeouts;
  }): Promise<ProviderResult>;
}

export interface Providers {
  gemini?: GeminiClient;
  claude?: ClaudeClient;
}
