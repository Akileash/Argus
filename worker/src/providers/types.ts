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

export interface GeminiClient {
  /** Upload the MP4 (Files API), wait until ACTIVE, then generate JSON constrained by `schema`. */
  generateWithVideo(args: {
    model: string;
    systemInstruction: string;
    prompt: string;
    video: Uint8Array;
    mimeType: string;
    displayName: string;
    schema: Record<string, unknown>;
  }): Promise<ProviderResult>;
}

export interface ClaudeClient {
  generateJson(args: {
    model: string;
    system: string;
    prompt: string;
    schema: Record<string, unknown>;
  }): Promise<ProviderResult>;
}

export interface Providers {
  gemini?: GeminiClient;
  claude?: ClaudeClient;
}
