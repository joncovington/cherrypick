import { Card } from "../../components/DataTable";
import { LockHero } from "../../pages/Config/LockHero";
import { ConfigSection } from "../../pages/Config/ConfigSection";
import { SECTIONS } from "../../pages/Config/fieldMeta";
import { useConfigModel, useLockStatus } from "../../pages/Config/useConfigModel";
import { useBoolPref, writePref } from "../../lib/prefs";
import { clearAllStaged, useDirtyCount } from "../../pages/Config/stagedStore";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/**
 * Config as a lightbox (2026-09): one slide per section (`fieldMeta.ts`'s own `SECTIONS`, the
 * same grouping the standalone page stacked as cards -- `arms`, `modules`, `timing`, `notify`,
 * `dev` -- plus a `prefs` slide for the browser-local console preferences) instead of scrolling
 * past all six. `LockHero` -- the live-trading halt toggle, this page's one bounded write
 * exception -- rides `persistentTop` rather than a slide: it is the single highest-stakes control
 * in the app (asymmetric friction by design -- set is one click, clear needs a typed
 * confirmation), so it stays visible on every section rather than a click away.
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

export function ConfigLightbox({ slide }: { slide: string }) {
  const model = useConfigModel();
  const lock = useLockStatus();
  const dirty = useDirtyCount();

  const failed = Object.entries(model.data?.targets ?? {}).filter(([, t]) => t.error !== undefined);

  const slides: SlideDef[] = [
    ...SECTIONS.map((s) => ({
      id: s.id,
      label: s.title.toLowerCase(),
      render: () => (
        <div className="cards cards-wide">
          <ConfigSection section={s} model={model.data} updatedAt={model.dataUpdatedAt} meicRiskDirty={lock.data?.meicRiskDirty ?? null} />
        </div>
      ),
    })),
    {
      id: "prefs",
      label: "prefs",
      render: () => (
        <div className="cards cards-wide">
          <Card title="Console preferences" collapseKey="config-prefs" updatedAt={model.dataUpdatedAt}>
            <p className="cfg-section-blurb muted">
              This browser's own display choices, kept in the console's store. They save as you change them —
              nothing here reaches another package.
            </p>
            <div className="cfg-fields">
              {PREFS.map((p) => (
                <PrefToggle key={p.key} pref={p} />
              ))}
            </div>
          </Card>
        </div>
      ),
    },
  ];

  return (
    <LightboxFrame
      module="config"
      slide={slide}
      slides={slides}
      session={null}
      badge={lock.data?.halted === true ? <span className="chip chip-warn">live halted</span> : undefined}
      headerControls={
        dirty > 0 ? (
          <>
            <span className="chip chip-warn">
              {dirty} unsaved change{dirty === 1 ? "" : "s"}
            </span>
            <button type="button" className="btn btn-quiet" onClick={clearAllStaged}>
              discard all
            </button>
          </>
        ) : undefined
      }
      persistentTop={
        <div className="lb-persistent">
          <LockHero />
          {model.isError && (
            <p className="stale-note">
              Could not read the config model. The lock above still works — it does not go through the config
              editor.
            </p>
          )}
          {failed.length > 0 && (
            <p className="stale-note">
              Not editable right now: {failed.map(([id]) => id).join(", ")} — {failed[0]?.[1].error}
            </p>
          )}
          <p className="fine-label muted cfg-footnote">
            Edits go through the orchestrator's own config editor, which backs up each file before writing and
            refuses the guarded live-trading fields in either direction. Arming live trading is not something this
            page can do.
          </p>
        </div>
      }
    />
  );
}
