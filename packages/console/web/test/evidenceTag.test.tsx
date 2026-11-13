import { describe, expect, it } from "vitest";
import { renderToString } from "react-dom/server";
import { ActionBlock } from "../src/components/ActionBlock";
import { EVIDENCE_WORDS, EvidenceTag, evidenceText } from "../src/components/EvidenceTag";

/**
 * A rule's evidence grade (2026-11-13) replaces the hand-set confidence number. The guards: a grade
 * shows as a word with its meaning on hover, never a number; an evaluation from before grades still
 * shows its old number, labelled as such; a hold (no grade, no number) shows nothing.
 */

describe("the evidence tag", () => {
  it("shows the grade as a word, with what it means", () => {
    const html = renderToString(<EvidenceTag evidence="contested" confidence={null} />);
    expect(html).toContain("evidence: contested");
    expect(html).toContain(EVIDENCE_WORDS["contested"]);
  });

  it("prefers the grade when an evaluation carries both", () => {
    expect(evidenceText("tested", 80)).toBe("evidence: tested");
  });

  it("shows an old evaluation's number, said to be old", () => {
    const html = renderToString(<EvidenceTag evidence={null} confidence={65} />);
    expect(html).toContain("confidence 65");
    expect(html).toContain("before evidence grades");
  });

  it("shows nothing for a hold", () => {
    expect(renderToString(<EvidenceTag evidence={null} confidence={null} />)).toBe("");
  });

  it("knows every grade the ruleset can give", () => {
    expect(Object.keys(EVIDENCE_WORDS).sort()).toEqual(["contested", "mechanics", "ours", "practice", "tested"]);
  });

  it("heads an action block", () => {
    const html = renderToString(<ActionBlock rule="credit.stop" evidence="contested" reason="loss reached the stop" />);
    expect(html).toContain("evidence: contested");
    expect(html).not.toContain("confidence");
  });
});
