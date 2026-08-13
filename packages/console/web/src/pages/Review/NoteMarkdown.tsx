import type { ReactNode } from "react";

/**
 * The narrative note, rendered as elements.
 *
 * Deliberately builds React nodes rather than setting HTML. The note is model-generated text read
 * off disk, so putting it through `dangerouslySetInnerHTML` would make any prose the agent emits —
 * or anything that ever lands in that file — executable in this page. Building elements makes that
 * structurally impossible, and React escapes the text for free.
 *
 * Deliberately tiny, and not a markdown library. The input is our own prompt's output with a known,
 * narrow shape: paragraphs, `**bold**` spans, one `## Recommendations` heading, and `- ` bullets
 * under it. Anything richer renders as plain text rather than being half-parsed, which is the
 * failure mode a hand-rolled markdown parser usually has. Three shapes get dedicated treatment
 * because the model reliably produces them and they carry real structure worth showing:
 *
 * - A paragraph that is nothing but one bold span (`**What happened**` alone on its own line)
 *   is a section label, not emphasis — rendered as a divider, the same visual the module cards
 *   already use for "By arm".
 * - A bold span that is itself a signed figure (`**+$80,102**`) or contains one (`**open +0.216**`)
 *   is a number the reader's eye should catch — tinted with the same pnl-pos/pnl-neg vocabulary
 *   the rest of the console already uses, never a new color.
 * - A bullet under Recommendations is `**Title.** body`, matching what the prompt actually asks
 *   for — split into a numbered card rather than a bare bullet. The literal "None — ..." bullet
 *   the prompt specifies for an empty day renders as plain muted text, not a card with nothing in it.
 *
 * Only '+' and the true minus sign (−, U+2212) count as a figure's sign — an ASCII hyphen does not,
 * so "width-5" is never mistaken for a negative number.
 */

const SIGNED_NUMBER = /[+−]\$?\d/;
const WHOLE_FIGURE = /^[+−]?\$?[\d][\d,]*(\.\d+)?[%×]?$/;
const SECTION_LABEL = /^\*\*([^*]+)\*\*$/;
const REC_ITEM = /^\*\*(.+?)\*\*\s*(.*)$/;

function figureNode(inner: string, key: string): ReactNode {
  const signed = inner.match(SIGNED_NUMBER);
  const classes = [signed ? (signed[0][0] === "+" ? "note-figure-pos" : "note-figure-neg") : "", WHOLE_FIGURE.test(inner) ? "note-figure" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <strong key={key} className={classes || undefined}>
      {inner}
    </strong>
  );
}

/** `**bold**` spans get figure treatment when they look like one; everything else is literal text. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return figureNode(part.slice(2, -2), `${keyPrefix}-${i}`);
    }
    return part;
  });
}

function RecItem({ text, index }: { text: string; index: number }) {
  if (/^None\b/.test(text)) {
    return <p className="note-rec-none muted">{text}</p>;
  }
  const m = text.match(REC_ITEM);
  const title = m ? (m[1] ?? text) : text;
  const body = m ? (m[2] ?? "") : "";
  return (
    <li className="note-rec">
      <span className="note-rec-index">{String(index).padStart(2, "0")}</span>
      <div>
        <div className="note-rec-title">{title}</div>
        {body && <div className="note-rec-body">{inline(body, `rec${index}`)}</div>}
      </div>
    </li>
  );
}

export function NoteMarkdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  let inRecs = false;

  const flushBullets = () => {
    if (bullets.length === 0) return;
    if (inRecs) {
      const items = bullets;
      const only = items.length === 1 ? items[0] : undefined;
      if (only !== undefined && /^None\b/.test(only)) {
        blocks.push(<RecItem key={`recs-${blocks.length}`} text={only} index={1} />);
      } else {
        blocks.push(
          <ul className="note-recs" key={`recs-${blocks.length}`}>
            {items.map((b, i) => (
              <RecItem key={i} text={b} index={i + 1} />
            ))}
          </ul>,
        );
      }
    } else {
      blocks.push(
        <ul className="note-list" key={`ul-${blocks.length}`}>
          {bullets.map((b, i) => (
            <li key={i}>{inline(b, `b${blocks.length}-${i}`)}</li>
          ))}
        </ul>,
      );
    }
    bullets = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    // The file's own header block — the page already states the provenance above it.
    if (line.startsWith("# ") || line.startsWith("---") || line.trim() === "") {
      flushBullets();
      continue;
    }
    if (line.startsWith("- ")) {
      bullets.push(line.slice(2));
      continue;
    }
    flushBullets();
    if (line.startsWith("## ")) {
      const label = line.slice(3);
      inRecs = /recommend/i.test(label);
      blocks.push(
        <div className="note-recs-head" key={`h-${blocks.length}`}>
          <span className="note-recs-marker" />
          {label}
        </div>,
      );
      continue;
    }
    // The generated header carries the provenance in italics; the page says it already.
    if (line.startsWith("_") && line.endsWith("_")) continue;

    const section = line.trim().match(SECTION_LABEL);
    if (section) {
      blocks.push(
        <div className="review-subhead note-section" key={`s-${blocks.length}`}>
          {section[1]}
        </div>,
      );
      continue;
    }
    blocks.push(<p key={`p-${blocks.length}`}>{inline(line, `p${blocks.length}`)}</p>);
  }
  flushBullets();

  return <div className="note">{blocks}</div>;
}
