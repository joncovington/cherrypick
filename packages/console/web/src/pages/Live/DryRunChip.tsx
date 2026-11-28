import { useState } from "react";
import type { LiveDryRun } from "@console/shared";

/**
 * A live loop's dry-run posture beside its armed chip, shown whether or not the loop is armed. The
 * value is the loop's own `--status` answer, read on the server; this file only words it. An unreadable
 * status is "unknown", never "dry run": a loop whose status cannot be read must not look safe.
 */

export function dryRunChipWords(state: LiveDryRun): { text: string; cls: string } {
  if (state === "live") return { text: "live", cls: "chip chip-warn" };
  if (state === "dry-run") return { text: "dry run", cls: "chip" };
  return { text: "unknown", cls: "chip muted" };
}

/** The ? tip's plain-words explanation. */
export function DryRunTip() {
  return (
    <>
      <p>
        <b>Dry run.</b> An armed loop builds each new entry and checks it against the real account, and sends nothing.
      </p>
      <p>
        <b>What it does not cover.</b> A position the loop already holds is still managed, so its stops, closes and
        settlement go through.
      </p>
      <p>
        <b>How it changes.</b> By hand in the module&rsquo;s config (<code>live.dry_run</code>), never from the console.
      </p>
      <p>
        <b>The other states.</b> Live sends real entries. Unknown means the loop&rsquo;s status could not be read.
      </p>
    </>
  );
}

export function DryRunChip({ state }: { state: LiveDryRun }) {
  const [open, setOpen] = useState(false);
  const w = dryRunChipWords(state);
  return (
    <span className="lt-dryrun">
      <span className={w.cls} data-dry-run={state}>
        {w.text}
      </span>
      <span className="grp-tipwrap">
        <button
          type="button"
          className={open ? "wiz-q is-open" : "wiz-q"}
          aria-label="What is a dry run?"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          ?
        </button>
        {open && (
          <span className="grp-tip-pop is-left lt-dryrun-tip" role="note">
            <DryRunTip />
          </span>
        )}
      </span>
    </span>
  );
}
