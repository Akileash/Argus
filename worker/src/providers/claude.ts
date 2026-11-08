import Anthropic from "@anthropic-ai/sdk";
import { withDeadline } from "./timeout";
import type { ClaudeClient, ProviderResult } from "./types";

export function createClaudeClient(apiKey: string): ClaudeClient {
  if (!apiKey) throw new Error("ANTHROPIC_API_KEY is blank: set it to use REPORT_MODE=hybrid");
  const client = new Anthropic({ apiKey });

  return {
    async generateJson({ model, system, prompt, schema, timeouts }): Promise<ProviderResult> {
      // Stream and take the final message: adaptive thinking + a long structured report can exceed
      // non-streaming HTTP timeouts. Server-side refusal fallbacks per spec 6.1.
      return withDeadline(timeouts?.callMs ?? 180_000, "Claude request", (signal) => generate(signal));

      async function generate(signal: AbortSignal): Promise<ProviderResult> {
      const stream = client.beta.messages.stream({
        model,
        max_tokens: 32000,
        thinking: { type: "adaptive" },
        output_config: { effort: "high", format: { type: "json_schema", schema } },
        betas: ["server-side-fallback-2026-07-01"],
        fallbacks: "default",
        system,
        messages: [{ role: "user", content: prompt }],
      }, { signal });
      const msg = await stream.finalMessage();

      if (msg.stop_reason === "refusal") {
        throw new Error(`Claude refused the request (category=${msg.stop_details?.category ?? "unknown"}) and the fallback chain also declined`);
      }
      if (msg.stop_reason === "max_tokens") {
        throw new Error("Claude hit max_tokens before finishing the report JSON");
      }
      const text = msg.content
        .filter((b): b is Extract<typeof b, { type: "text" }> => b.type === "text")
        .map((b) => b.text)
        .join("");
      if (!text) throw new Error("Claude returned no text block");
      return {
        text,
        inputTokens: msg.usage.input_tokens,
        outputTokens: msg.usage.output_tokens,
        model: msg.model,
      };
      }
    },
  };
}
