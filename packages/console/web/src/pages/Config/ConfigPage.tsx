import { Card } from "../../components/DataTable";
import { LockHero } from "./LockHero";
import { ConfigSection } from "./ConfigSection";
import { SECTIONS } from "./fieldMeta";
import { useConfigModel, useLockStatus } from "./useConfigModel";
import { useBoolPref, writePref } from "../../lib/prefs";
import { clearAllStaged, useDirtyCount } from "./stagedStore";

/**
 * The suite's write surface: the live lock, then the settings that actually change between
 * sessions. Everything here is either the halt flag or a field this page's metadata names — the
 * rest of the configs stay in a text editor, with their own notes in view.
 */

const PREFS: Array<{ key: string; label: string; help: string }> = [
  { key: "denseTables", label: "Dense tables", help: "Tighter row height across the read pages." },
  {
    key: "defaultLiveMode",
    label: "Default to live tab",
    help: "Open module pages on live rather than paper. A ?mode= in the URL always wins over this.",
  },
];

function PrefToggle({ pref }: { pref: { key: string; label: string; help: string } }) {
  const on = useBoolPref(pref.key);
  return (
    <div className="cfg-row">
      <div className="cfg-row-label">
        <label>{pref.label}</label>
        <p className="cfg-help">{pref.help}</p>
      </div>
      <div className="cfg-row-control">
        <div className="mode-toggle" role="group" aria-label={pref.label}>
          <button type="button" className={`mode-btn ${!on ? "active" : ""}`} onClick={() => void writePref(pref.key, false)}>
            off
          </button>
          <button type="button" className={`mode-btn ${on ? "active" : ""}`} onClick={() => void writePref(pref.key, true)}>
            on
          </button>
        </div>
      </div>
    </div>
  );
}

function ConsolePrefsCard({ updatedAt }: { updatedAt?: number }) {
  return (
    <Card title="Console preferences" collapseKey="config-prefs" updatedAt={updatedAt}>
      <p className="cfg-section-blurb muted">
        This browser's own display choices, kept in the console's store. They save as you change them — nothing
        here reaches another package.
      </p>
      <div className="cfg-fields">
        {PREFS.map((p) => (
          <PrefToggle key={p.key} pref={p} />
        ))}
      </div>
    </Card>
  );
}

export function ConfigPage() {
  const model = useConfigModel();
  const lock = useLockStatus();
  const dirty = useDirtyCount();

  const failed = Object.entries(model.data?.targets ?? {}).filter(([, t]) => t.error !== undefined);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Config</h1>
        {lock.data?.halted === true && <span className="chip chip-warn">live halted</span>}
        {dirty > 0 && (
          <>
            <span className="chip chip-warn">
              {dirty} unsaved change{dirty === 1 ? "" : "s"}
            </span>
            <button type="button" className="btn btn-quiet" onClick={clearAllStaged}>
              discard all
            </button>
          </>
        )}
      </div>

      <LockHero />

      {model.isError && (
        <p className="stale-note">
          Could not read the config model. The lock above still works — it does not go through the config editor.
        </p>
      )}
      {failed.length > 0 && (
        <p className="stale-note">
          Not editable right now: {failed.map(([id]) => id).join(", ")} — {failed[0]?.[1].error}
        </p>
      )}

      <div className="cards cards-wide">
        {SECTIONS.map((s) => (
          <ConfigSection
            key={s.id}
            section={s}
            model={model.data}
            updatedAt={model.dataUpdatedAt}
            meicRiskDirty={lock.data?.meicRiskDirty ?? null}
          />
        ))}
        <ConsolePrefsCard updatedAt={model.dataUpdatedAt} />
      </div>

      <p className="fine-label muted cfg-footnote">
        Edits go through the orchestrator's own config editor, which backs up each file before writing and refuses
        the guarded live-trading fields in either direction. Arming live trading is not something this page can do.
      </p>
    </div>
  );
}
