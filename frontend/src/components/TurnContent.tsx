import type { ReactNode } from "react";
import { useT } from "../i18n";

/** The LLM-authored content of one tool call, rendered as that assistant
 *  turn's body — the ledger view: ANSWER is chat prose, BASH is a command
 *  line, everything else (file bodies, message drafts) is quoted material,
 *  collapsed when long. */
export function TurnContent({ op, text }: { op: string; text: string }) {
  const t = useT();
  const lines = text.split("\n").length;

  if (op === "ANSWER" || op === "LLM_REPLY") {
    return (
      <div
        data-testid="assistant-message"
        className="whitespace-pre-wrap border-t border-line px-3.5 py-3 text-sm leading-relaxed text-ink"
      >
        {text}
      </div>
    );
  }

  if (op === "BASH") {
    return (
      <pre
        data-testid="turn-command"
        className="num overflow-x-auto border-t border-line px-3.5 py-2 text-xs leading-relaxed text-ink"
      >
        <span className="text-accent">$ </span>{text}
      </pre>
    );
  }

  const short = text.length <= 400 && lines <= 10;
  return (
    <details open={short} className="border-t border-line">
      <summary className="cursor-pointer list-none px-3.5 py-1.5 text-[11px] text-ink2 select-none">
        {t("authoredContent", { n: text.length, l: lines })}
      </summary>
      <div className="whitespace-pre-wrap border-t border-line/60 px-3.5 py-2.5 text-xs leading-relaxed text-ink2 max-h-64 overflow-auto">
        {text}
      </div>
    </details>
  );
}

export function Badge({ tone, title, children }: {
  tone: "accent" | "good" | "critical" | "neutral" | "warn";
  title?: string;
  children: ReactNode;
}) {
  const tones: Record<string, string> = {
    accent: "border-accent/40 text-accent",
    good: "border-good/50 text-good",
    critical: "border-critical/50 text-critical",
    warn: "border-warn/60 text-ink",
    neutral: "border-line text-ink2",
  };
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}
