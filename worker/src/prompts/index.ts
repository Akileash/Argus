import { readFileSync } from "node:fs";

function load(name: string): string {
  return readFileSync(new URL(`./${name}`, import.meta.url), "utf8").trim();
}

/** System prompt for whichever model writes the report (Gemini in mode gemini, Claude in mode hybrid). */
export const WRITER_SYSTEM_PROMPT = load("writer_system.md");
/** System prompt for Gemini stage 1 in hybrid mode (video -> observations only). */
export const OBSERVE_SYSTEM_PROMPT = load("observe_system.md");
