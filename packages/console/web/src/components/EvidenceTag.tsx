/**
 * A rule's evidence grade (packages/manage `rules.EVIDENCE_GRADES`, 2026-11-13): the kind of evidence
 * behind the rule, never a number to rank by. An evaluation written before grades carries the old
 * `confidence` number instead, and shows that, labelled as such.
 */

/** What each grade means, as the ruleset states it (hover text). */
export const EVIDENCE_WORDS: Record<string, string> = {
  mechanics: "follows from exchange or option mechanics, or plain arithmetic",
  tested: "a published test, or our own declared study, supports it",
  contested: "tests exist and point against it, or are mixed",
  practice: "practitioner doctrine (a vendor, a guide), with no test",
  ours: "our own rule, with no test",
};

/** The words a card shows for a rule's grade: "evidence: tested", or an old evaluation's
 *  "confidence 65"; null when there is neither (a hold). */
export function evidenceText(evidence: string | null | undefined, confidence?: number | null): string | null {
  if (evidence) return `evidence: ${evidence}`;
  if (confidence !== null && confidence !== undefined) return `confidence ${confidence}`;
  return null;
}

export function EvidenceTag({
  evidence,
  confidence,
  className,
}: {
  evidence: string | null | undefined;
  confidence?: number | null;
  className?: string;
}) {
  const text = evidenceText(evidence, confidence);
  if (text === null) return null;
  const title = evidence
    ? (EVIDENCE_WORDS[evidence] ?? evidence)
    : "from an evaluation written before evidence grades: the rule's old confidence number";
  return (
    <span className={`muted${className ? ` ${className}` : ""}`} title={title}>
      {text}
    </span>
  );
}
