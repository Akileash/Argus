// The prompt texts are .ts modules (not .md files read at runtime) so they are bundled into the Next/Vercel build.

/** System prompt for whichever model writes the report (Gemini in mode gemini, Claude in mode hybrid). */
export { WRITER_SYSTEM_PROMPT } from "./writer_system";
/** System prompt for Gemini stage 1 in hybrid mode (video -> observations only). */
export { OBSERVE_SYSTEM_PROMPT } from "./observe_system";
