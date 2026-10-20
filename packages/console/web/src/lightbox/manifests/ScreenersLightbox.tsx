import { EarningsCalendarTab } from "../../pages/Screeners/EarningsCalendarTab";
import { EarningsRecordTab } from "../../pages/Screeners/EarningsRecordTab";
import { IncomeTab } from "../../pages/Screeners/IncomeTab";
import { NeedsDolt } from "../../pages/Screeners/NeedsDolt";
import { ScreenerTab } from "../../pages/Screeners/ScreenersPage";
import { SignalBoardTab } from "../../pages/Screeners/SignalBoardTab";
import { SkateTab } from "../../pages/Screeners/SkateTab";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The income screener lists (2026-10-16): one tab per list; the earnings calendar (2026-10-17); the signal board (2026-10-17); and
 * the skate screen (2026-10-19). This manifest holds the page list and nothing else.
 */
const slides: SlideDef[] = [
  // Ours by default, the vendor's saved days behind a toggle (2026-10-19). Credit spreads stay the vendor's.
  { id: "short-puts", label: "short puts", render: () => <IncomeTab list="short-puts" /> },
  { id: "covered-calls", label: "covered calls", render: () => <IncomeTab list="covered-calls" /> },
  { id: "credit-spreads", label: "credit spreads", render: () => <ScreenerTab list="credit-spreads" /> },
  { id: "earnings", label: "earnings", render: () => (
      <NeedsDolt slide="earnings" label="earnings">
        <EarningsCalendarTab />
      </NeedsDolt>
    ) },
  { id: "earnings-record", label: "earnings record", render: () => (
      <NeedsDolt slide="earnings-record" label="earnings record">
        <EarningsRecordTab />
      </NeedsDolt>
    ) },
  { id: "board", label: "signal board", render: () => (
      <NeedsDolt slide="board" label="signal board">
        <SignalBoardTab />
      </NeedsDolt>
    ) },
  { id: "skate", label: "skate", render: () => (
      <NeedsDolt slide="skate" label="skate">
        <SkateTab />
      </NeedsDolt>
    ) },
];

export function ScreenersLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="screeners" slide={slide} slides={slides} session={null} />;
}
