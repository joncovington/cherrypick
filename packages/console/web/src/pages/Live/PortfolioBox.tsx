import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { ManageAccount } from "@console/shared";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { BandsOverTime } from "./BandsHistory";
import { LimitsBody } from "./LimitsCard";
import { BrokerCheckBody, ContributionBars, DeltaIdeasBody, HedgeBody, deltaIdeasFirst } from "./PortfolioCards";
import { PositionCard } from "./PositionCards";
import { PremiumPaceBody } from "./PremiumPace";
import { RiskGraphBody } from "./ScenarioCards";
import { dayMonth } from "./TradeCard";
import { PortfolioBoxContext, expiryBoxId } from "./portfolioBoxContext";

/**
 * The portfolio tab's lightboxes (2026-11-06): what explains or audits a card opens from it instead of
 * sitting on the page as a card of its own. One is open at a time, named in the URL (`?pf=<id>`), so a
 * link opens it and the back button closes it; ← → step through every one the account has. The cards
 * reach `open` through the context, whose default does nothing, so a card renders alone in a test.
 */

export interface Box {
  id: string;
  kicker: string;
  title: string;
  tabs: LightboxTab[];
  body: (tab: string) => ReactNode;
}

function boxesFor(a: ManageAccount, rules: Record<string, unknown> | null | undefined, openTrade: (id: string) => void): Box[] {
  const p = a.portfolio;
  if (!p) return [];
  const one = (id: string, label: string): LightboxTab[] => [{ id, label }];
  const out: Box[] = [];
  if (a.premium) {
    out.push({ id: "pace", kicker: "income ›", title: "Premium pace", tabs: one("pace", "by month"), body: () => <PremiumPaceBody premium={a.premium!} thetaBand={p.bands?.theta ?? null} /> });
  }
  out.push({
    id: "overTime",
    kicker: "income · direction · room to act ›",
    title: "Over time",
    tabs: [
      { id: "all", label: "all three" },
      { id: "delta", label: "delta" },
      { id: "theta", label: "theta" },
      { id: "bp", label: "buying power" },
    ],
    body: (tab) => <BandsOverTime account={a.account} only={tab === "all" ? null : tab} />,
  });
  out.push({ id: "thetaAll", kicker: "where the theta comes from ›", title: "Every name's theta", tabs: one("all", "by size"), body: () => <ContributionBars p={p} figure="theta" limit={null} /> });
  out.push({ id: "deltaAll", kicker: "where the delta comes from ›", title: "Every name's delta", tabs: one("all", "by size"), body: () => <ContributionBars p={p} figure="bwd" limit={null} /> });
  if (p.brokerCheck) {
    out.push({ id: "broker", kicker: "where the delta comes from ›", title: "Against the broker's model", tabs: one("check", "delta and theta"), body: () => <BrokerCheckBody p={p} /> });
  }
  if (p.deltaIdeas) {
    const d = p.deltaIdeas;
    const tab = (dir: "add" | "cut"): LightboxTab => ({ id: dir, label: `${dir === "add" ? "add" : "cut"} delta · ${d[dir].length}` });
    const first = deltaIdeasFirst(d);
    out.push({
      id: "deltaIdeas",
      kicker: "direction ›",
      title: "Moving delta without closing",
      tabs: first === "add" ? [tab("add"), tab("cut")] : [tab("cut"), tab("add")],
      body: (t) => <DeltaIdeasBody d={d} direction={t === "cut" ? "cut" : "add"} />,
    });
  }
  if (p.hedges) {
    out.push({ id: "hedge", kicker: "direction ›", title: "A hedge, sized", tabs: one("hedge", `${p.hedges.suggestions.length} structure${p.hedges.suggestions.length === 1 ? "" : "s"}`), body: () => <HedgeBody p={p} /> });
  }
  if (p.scenarios) {
    out.push({ id: "graph", kicker: "if the market moves ›", title: "Risk graph", tabs: one("graph", "now and later"), body: () => <RiskGraphBody p={p} /> });
  }
  if (a.limits) {
    out.push({
      id: "limits",
      kicker: "room to act ›",
      title: "Your limits",
      tabs: [
        { id: "limits", label: "limits" },
        { id: "ruleset", label: "ruleset" },
      ],
      body: (tab) =>
        tab === "ruleset" ? (
          <pre className="pf-ruleset">{rules ? JSON.stringify(rules, null, 2) : "—"}</pre>
        ) : (
          <LimitsBody limits={a.limits!} />
        ),
    });
  }
  for (const x of p.ladder?.expiries ?? []) {
    const trades = a.trades.filter((t) => x.trades.includes(t.tradeId));
    if (trades.length === 0) continue;
    out.push({
      id: expiryBoxId(x.expiration),
      kicker: "what expires when ›",
      title: `${dayMonth(x.expiration)} · ${trades.length} trade${trades.length === 1 ? "" : "s"}`,
      tabs: one("trades", "trades"),
      body: () => (
        <>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            can still lose {x.downside === null ? "—" : `$${Math.abs(Math.round(x.downside)).toLocaleString("en-US")}`} (modelled) · each card opens
            its trade on the positions tab
          </p>
          <div className="pos-cards">
            {trades.map((t) => (
              <PositionCard key={t.tradeId} t={t} onOpen={() => openTrade(t.tradeId)} />
            ))}
          </div>
        </>
      ),
    });
  }
  return out;
}

export function BoxDialog({ boxes, index, onStep, onClose }: { boxes: Box[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const box = boxes[index]!;
  const [tabState, setTab] = useState<{ id: string; tab: string }>({ id: box.id, tab: box.tabs[0]?.id ?? "" });
  // A step to another lightbox opens on its first tab.
  const tab = tabState.id === box.id ? tabState.tab : (box.tabs[0]?.id ?? "");
  return (
    <CardLightbox count={boxes.length} index={index} onStep={onStep} onClose={onClose} wide>
      {(nav) => (
        <LightboxFrame
          kicker={box.kicker}
          title={box.title}
          tabs={box.tabs}
          tab={tab}
          onTab={(t) => setTab({ id: box.id, tab: t })}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          <div className="pf-box-body">{box.body(tab)}</div>
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The provider and the open lightbox, around one account's portfolio cards. */
export function PortfolioBoxes({ a, rules, children }: { a: ManageAccount; rules: Record<string, unknown> | null | undefined; children: ReactNode }) {
  const lb = useOpenParam("pf");
  const navigate = useNavigate();
  const boxes = boxesFor(a, rules, (id) => navigate(`/live/positions?trade=${encodeURIComponent(id)}`));
  const ids = new Set(boxes.map((b) => b.id));
  const index = boxes.findIndex((b) => b.id === lb.open);
  return (
    <PortfolioBoxContext.Provider value={{ open: lb.show, has: (id) => ids.has(id) }}>
      {children}
      {index >= 0 ? <BoxDialog boxes={boxes} index={index} onStep={(to) => lb.step(boxes[to]?.id ?? "")} onClose={lb.close} /> : null}
    </PortfolioBoxContext.Provider>
  );
}
