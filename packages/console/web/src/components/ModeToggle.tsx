import type { TradingMode } from "@console/shared";

export function ModeToggle({
  mode,
  onChange,
}: {
  mode: TradingMode;
  onChange: (mode: TradingMode) => void;
}) {
  return (
    <div className="mode-toggle" role="group" aria-label="trading mode">
      <span className={`mode-pill ${mode === "live" ? "mode-pill-live" : ""}`} aria-hidden="true" />
      <button
        type="button"
        className={mode === "paper" ? "mode-btn active" : "mode-btn"}
        onClick={() => onChange("paper")}
      >
        Paper
      </button>
      <button
        type="button"
        className={mode === "live" ? "mode-btn active mode-btn-live" : "mode-btn"}
        onClick={() => onChange("live")}
      >
        Live
      </button>
    </div>
  );
}
