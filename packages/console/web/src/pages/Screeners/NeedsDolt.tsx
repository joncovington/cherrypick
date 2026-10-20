import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useFeatures } from "../../lib/useFeatures";
import { screenerOffReason } from "../../lib/visibility";

const INSTALL_DOC = "https://github.com/joncovington/cherrypick/blob/main/INSTALL.md#what-the-installer-does";

/**
 * A Dolt-backed Screeners tab, or -- when the suite says its engines are off -- what it needs and how
 * to get it (`visibility.screenerOffReason`). The tab is never hidden and never an empty table: a
 * blank screen reads as a quiet day.
 */
export function NeedsDolt({ slide, label, children }: { slide: string; label: string; children: ReactNode }) {
  const off = screenerOffReason(slide, useFeatures());
  if (off === null) return <>{children}</>;
  const noDolt = off.needs === "dolt";
  return (
    <section className="card module-off">
      <div className="card-head">
        <h2>{noDolt ? `The ${label} tab needs Dolt` : `The ${label} tab is turned off`}</h2>
      </div>
      <p>{off.reason}</p>
      {noDolt ? (
        <>
          <p>To set it up:</p>
          <ol>
            <li>
              Run the installer again (<code>install.cmd</code> on Windows, <code>./install.sh</code> elsewhere) and say yes to the Dolt
              setup. It downloads three free public datasets from DoltHub, several GB, which can take an hour.
            </li>
            <li>
              Or, with Dolt already installed and the clones in place, record it: <code>python packages/orchestrator/run.py capabilities --detect --write</code>.
            </li>
            <li>The tab fills after the next evening&apos;s technicals run.</li>
          </ol>
          <p className="muted">
            The steps are in{" "}
            <a className="link" href={INSTALL_DOC} target="_blank" rel="noreferrer">
              INSTALL.md
            </a>
            . The short-put and covered-call lists do not need Dolt.
          </p>
        </>
      ) : (
        <p className="muted">
          Turn it back on from <Link className="link" to="/config">Config</Link>, or set <code>technicals.enabled</code> in the suite config.
        </p>
      )}
    </section>
  );
}
