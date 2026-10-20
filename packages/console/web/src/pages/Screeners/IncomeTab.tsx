import { useSearchParams } from "react-router-dom";
import type { IncomeList } from "@console/shared";
import { OwnListTab } from "./OwnListTab";
import { ScreenerTab } from "./ScreenersPage";

/**
 * The short puts and covered calls tabs: our own list by default (2026-10-19), the vendor's saved
 * days one click away (`?source=vendor`) until they are retired. The two are different lists -- a
 * different universe, strike rule and expiry -- so they are never mixed on one table.
 */
export function IncomeTab({ list }: { list: IncomeList }) {
  const [params, setParams] = useSearchParams();
  const vendor = params.get("source") === "vendor";
  const choose = (v: boolean) => {
    const next = new URLSearchParams();
    if (v) next.set("source", "vendor");
    setParams(next, { replace: true });
  };
  return (
    <>
      <div className="mode-toggle" style={{ marginLeft: 0, marginBottom: 8 }} role="group" aria-label="Which list">
        <button type="button" className={vendor ? "mode-btn" : "mode-btn active"} onClick={() => choose(false)}>
          ours
        </button>
        <button
          type="button"
          className={vendor ? "mode-btn active" : "mode-btn"}
          onClick={() => choose(true)}
          title="The vendor's saved lists, from before its subscription ended (2026-10-24)"
        >
          vendor (saved days)
        </button>
      </div>
      {vendor ? <ScreenerTab list={list} /> : <OwnListTab list={list} />}
    </>
  );
}
