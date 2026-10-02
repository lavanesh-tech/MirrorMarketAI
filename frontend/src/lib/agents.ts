import { z } from "zod";

/*
 * Agent runs store their result as free-form JSON in the API contract, so each
 * shape the UI renders is checked here first (see also `comparison.ts`).
 */
const synthesisSchema = z.object({
  product_id: z.string(),
  product_name: z.string(),
  verdict: z.string(),
  score: z.number(),
  blockers: z.array(z.string()),
  concerns: z.array(z.string()),
  strengths: z.array(z.string()),
  summary: z.string(),
});

const stepSchema = z.object({
  agent: z.string(),
  status: z.enum(["SUCCEEDED", "FAILED", "SKIPPED"]),
  duration_ms: z.number(),
  engine: z.string().nullable().optional(),
  reason: z.string().nullable().optional(),
});

const orchestrationSchema = z.object({
  products: z.array(
    z.object({
      product_id: z.string(),
      steps: z.array(stepSchema),
      synthesis: synthesisSchema.nullable(),
    }),
  ),
  products_skipped: z.array(z.string()),
  time_budget_exhausted: z.boolean(),
  token_budget_exhausted: z.boolean(),
  elapsed_ms: z.number(),
});

export type Orchestration = z.infer<typeof orchestrationSchema>;
export type AgentStep = z.infer<typeof stepSchema>;

export function parseOrchestration(output: unknown): Orchestration | null {
  const parsed = orchestrationSchema.safeParse(output);
  return parsed.success ? parsed.data : null;
}

const askSchema = z.object({
  question: z.string(),
  answer: z.string(),
  abstained: z.boolean(),
  message: z.string().nullable(),
  cited: z.array(z.string()),
  dropped_sentences: z.array(z.string()),
  evidence_items: z.number(),
});

export type AskAnswer = z.infer<typeof askSchema>;

export function parseAnswer(output: unknown): AskAnswer | null {
  const parsed = askSchema.safeParse(output);
  return parsed.success ? parsed.data : null;
}

const AGENT_LABELS: Record<string, string> = {
  product_research: "Specifications",
  review_intelligence: "Reviews",
  compatibility: "Compatibility",
  value: "Price and value",
  risk: "Warranty and risk",
  synthesis: "Verdict",
};

export function agentLabel(agent: string): string {
  return AGENT_LABELS[agent] ?? agent.replaceAll("_", " ");
}

export const STEP_WORDS: Record<AgentStep["status"], string> = {
  SUCCEEDED: "Done",
  FAILED: "Failed",
  SKIPPED: "Skipped",
};

/** "rules-v1" is the built-in offline engine; anything else is a language model. */
export function engineLabel(engine: string | null | undefined): string {
  if (!engine) return "";
  return engine.startsWith("rules") ? "built-in rules" : engine;
}

export type AnswerPart = { text: string } | { marker: string };

/** Split "RAM is 16 GB [E1][E2]." into text and citation markers, in order. */
export function splitCitations(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = [];
  let last = 0;
  for (const match of answer.matchAll(/\[(E\d+)\]/g)) {
    if (match.index > last) parts.push({ text: answer.slice(last, match.index) });
    parts.push({ marker: match[1] ?? "" });
    last = match.index + match[0].length;
  }
  if (last < answer.length) parts.push({ text: answer.slice(last) });
  return parts;
}

/** "product_research: required Memory not met" -> "Specifications: required Memory not met". */
export function finding(text: string): string {
  const match = /^([a-z_]+): (.*)$/s.exec(text);
  return match ? `${agentLabel(match[1] ?? "")}: ${match[2] ?? ""}` : text;
}
