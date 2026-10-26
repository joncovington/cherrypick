import { EarningsCalendarTab } from "../../pages/Screeners/EarningsCalendarTab";
import { EarningsRecordTab } from "../../pages/Screeners/EarningsRecordTab";
import { CreditSpreadTab } from "../../pages/Screeners/CreditSpreadTab";
import { NeedsDolt } from "../../pages/Screeners/NeedsDolt";
import { OwnListTab } from "../../pages/Screeners/OwnListTab";
import { SignalBoardTab } from "../../pages/Screeners/SignalBoardTab";
import { SkateTab } from "../../pages/Screeners/SkateTab";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Our income lists (short puts and covered calls 2026-10-19, credit spreads 2026-10-24): one tab per list; the earnings calendar (2026-10-17); the signal board (2026-10-17); and
 * the skate screen (2026-10-19). This manifest holds the page list and nothing else.
 */
export const slides: SlideDef[] = [
  // Our own lists only: the vendor's subscription ended 2026-10-24, and its saved days are no longer shown.
  { id: "short-puts", label: "short puts", render: () => <OwnListTab list="short-puts" /> },
  { id: "covered-calls", label: "covered calls", render: () => <OwnListTab list="covered-calls" /> },
  // The setups are read from the history store, which Dolt fills.
  { id: "credit-spreads", label: "credit spreads", render: () => (
      <NeedsDolt slide="credit-spreads" label="credit spreads">
        <CreditSpreadTab />
      </NeedsDolt>
    ) },
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
