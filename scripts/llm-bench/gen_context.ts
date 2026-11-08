// Dumps the worker's real prompts, schemas and per-case event prompt text to inputs/context.json.
// Run from worker/: pnpm exec tsx ../scripts/llm-bench/gen_context.ts
import { writeFileSync, mkdirSync } from "node:fs";
import { makeEvent, makeTrace, FIXTURE_EVENT_ID } from "@argus/shared";
import { LLM_SCHEMA, OBSERVATION_SCHEMA } from "../../worker/src/llm-schema";
import { OBSERVE_SYSTEM_PROMPT, WRITER_SYSTEM_PROMPT } from "../../worker/src/prompts/index";
import { eventPromptText } from "../../worker/src/pipeline/summary";

const mk = (type: "fall" | "struck_by_near_miss") => {
  const event = makeEvent(type);
  const trace = makeTrace(type);
  const ctx: any = {
    eventId: FIXTURE_EVENT_ID, event, siteName: "Example Project - Edmonton", workerName: "J. Smith",
    crewName: "Framing A", supervisorName: "A. Tremblay", deviceSerial: "ARGUS-001", clip: null, trace: null,
  };
  return { event_type: type, device_class: event.device_class, pre_s: event.window.pre_s, post_s: event.window.post_s, prompt_text: eventPromptText(ctx, trace) };
};
mkdirSync(new URL("./inputs/", import.meta.url), { recursive: true });
writeFileSync(
  new URL("./inputs/context.json", import.meta.url),
  JSON.stringify(
    {
      observe_system: OBSERVE_SYSTEM_PROMPT,
      writer_system: WRITER_SYSTEM_PROMPT,
      llm_schema: LLM_SCHEMA,
      observation_schema: OBSERVATION_SCHEMA,
      cases: { fall: mk("fall"), struck_by: mk("struck_by_near_miss") },
    },
    null,
    1,
  ),
);
console.log("ok");
