import { GoogleGenAI, createPartFromUri, createUserContent } from "@google/genai";
import type { GeminiClient, ProviderResult } from "./types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function createGeminiClient(apiKey: string): GeminiClient {
  if (!apiKey) throw new Error("GEMINI_API_KEY is blank: set it to use REPORT_MODE=gemini or hybrid");
  const ai = new GoogleGenAI({ apiKey });

  return {
    async generateWithVideo({ model, systemInstruction, prompt, video, mimeType, displayName, schema }): Promise<ProviderResult> {
      if (!model) {
        throw new Error("GEMINI_MODEL is blank: set it to the Gemini Flash model name from your Google AI Studio account (verify the current name at signup)");
      }
      // Files API upload, then poll until the video is processed (state ACTIVE).
      let file = await ai.files.upload({
        file: new Blob([video as BlobPart], { type: mimeType }),
        config: { mimeType, displayName },
      });
      try {
        const deadline = Date.now() + 5 * 60_000;
        while (file.state === "PROCESSING") {
          if (Date.now() > deadline) throw new Error("Gemini file processing timed out after 5 minutes");
          await sleep(2000);
          file = await ai.files.get({ name: file.name! });
        }
        if (file.state !== "ACTIVE" || !file.uri) {
          throw new Error(`Gemini file did not become ACTIVE (state=${file.state ?? "unknown"})`);
        }
        const res = await ai.models.generateContent({
          model,
          contents: createUserContent([createPartFromUri(file.uri, file.mimeType ?? mimeType), prompt]),
          config: {
            systemInstruction,
            responseMimeType: "application/json",
            responseJsonSchema: schema,
          },
        });
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
        if (file.name) await ai.files.delete({ name: file.name }).catch(() => undefined);
      }
    },
  };
}
