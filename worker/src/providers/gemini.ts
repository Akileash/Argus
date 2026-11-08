import { GoogleGenAI, createPartFromUri, createUserContent } from "@google/genai";
import { VideoUnusableError } from "../errors";
import { withDeadline } from "./timeout";
import { withTransientRetry } from "./transient";
import type { CallTimeouts, GeminiClient, ProviderResult } from "./types";

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export const DEFAULT_TIMEOUTS: CallTimeouts = { callMs: 180_000, processingMs: 120_000 };
const POLL_MS = 2000;
/** One Files API "get" or "delete" is a tiny request; do not let it hang the loop. */
const SMALL_CALL_MS = 30_000;

/** The slice of GoogleGenAI this client uses (lets tests inject a fake without network). */
export type GenAiLike = Pick<GoogleGenAI, "files" | "models">;

export interface GeminiClientOptions {
  ai?: GenAiLike;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

export function createGeminiClient(apiKey: string, opts: GeminiClientOptions = {}): GeminiClient {
  if (!apiKey && !opts.ai) throw new Error("GEMINI_API_KEY is blank: set it to use REPORT_MODE=gemini or hybrid");
  const ai: GenAiLike = opts.ai ?? new GoogleGenAI({ apiKey });
  const sleep = opts.sleep ?? defaultSleep;
  const now = opts.now ?? Date.now;

  return {
    async generateWithVideo({ model, systemInstruction, prompt, video, mimeType, displayName, schema, timeouts }): Promise<ProviderResult> {
      if (!model) {
        throw new Error("GEMINI_MODEL is blank: set it to the Gemini Flash model name from your Google AI Studio account (verify the current name at signup)");
      }
      const t = timeouts ?? DEFAULT_TIMEOUTS;
      // Files API upload, then poll until the video is processed (state ACTIVE).
      const retry = <T>(fn: () => Promise<T>) =>
        withTransientRetry({ tries: t.transient?.tries ?? 1, baseMs: t.transient?.baseMs ?? 0, sleep, onRetry: t.onTransientRetry }, fn);
      let file = await retry(() =>
        withDeadline(t.callMs, "Gemini file upload", (signal) =>
          ai.files.upload({
            file: new Blob([video as BlobPart], { type: mimeType }),
            config: { mimeType, displayName, abortSignal: signal },
          }),
        ),
      );
      try {
        const deadline = now() + t.processingMs;
        while (file.state === "PROCESSING") {
          if (now() >= deadline) {
            throw new VideoUnusableError(`Gemini was still processing the video after ${Math.round(t.processingMs / 1000)} s`);
          }
          await sleep(Math.max(0, Math.min(POLL_MS, deadline - now())));
          const name = file.name!;
          file = await withDeadline(SMALL_CALL_MS, "Gemini file status check", (signal) =>
            ai.files.get({ name, config: { abortSignal: signal } }),
          );
        }
        if (file.state === "FAILED") {
          throw new VideoUnusableError(`Gemini could not process the video (file state FAILED${file.error?.message ? `: ${file.error.message}` : ""})`);
        }
        if (file.state !== "ACTIVE" || !file.uri) {
          throw new VideoUnusableError(`Gemini file did not become ACTIVE (state=${file.state ?? "unknown"})`);
        }
        const uri = file.uri;
        const fileMime = file.mimeType ?? mimeType;
        const res = await retry(() =>
          withDeadline(t.callMs, "Gemini generate call", (signal) =>
            ai.models.generateContent({
              model,
              contents: createUserContent([createPartFromUri(uri, fileMime), prompt]),
              config: {
                systemInstruction,
                responseMimeType: "application/json",
                responseJsonSchema: schema,
                abortSignal: signal,
              },
            }),
          ),
        );
        const text = res.text;
        if (!text) throw new Error(`Gemini returned no text (finishReason=${res.candidates?.[0]?.finishReason ?? "unknown"})`);
        const u = res.usageMetadata;
        return {
          text,
          inputTokens: u?.promptTokenCount ?? 0,
          outputTokens: (u?.candidatesTokenCount ?? 0) + (u?.thoughtsTokenCount ?? 0),
          model: res.modelVersion ?? model,
        };
      } finally {
        // Always remove the uploaded file (the Files API keeps it for 48 h otherwise). Best effort, time-limited.
        const name = file.name;
        if (name) {
          await withDeadline(SMALL_CALL_MS, "Gemini file delete", (signal) => ai.files.delete({ name, config: { abortSignal: signal } })).catch(
            () => undefined,
          );
        }
      }
    },
  };
}
