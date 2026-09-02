import { useSearchParams } from "react-router-dom";
import { TabStrip } from "../../components/ScopeBar";
import { MorningPage } from "../Morning/MorningPage";
import { ReviewPage } from "../Review/ReviewPage";

/**
 * The suite's two session reports on one page: the pre-open morning pack and the end-of-day review.
 *
 * They are separate artifacts written by separate packages (`packages/overview` before the open,
 * `packages/review` after the close), and this page keeps them separate — it holds the tab and
 * nothing else. Each tab renders its own page component unchanged, with its own title, session
 * picker and fact-version chip, so neither report gains a second place where its shape is decided.
 *
 * **The tab lives in the URL** (`?tab=eod`), not just in state. A report you are reading is a thing
 * you send someone, and the session pickers inside each page already assume a linkable surface —
 * a tab held only in memory would make "the EOD for the 14th" unlinkable. The old `/morning` and
 * `/review` routes redirect here rather than 404, since both are in the suite's own docs.
 */

const TABS = ["morning", "eod"] as const;
type ReportTab = (typeof TABS)[number];

const LABELS: Record<ReportTab, string> = { morning: "Morning", eod: "EOD" };

export function ReportsPage() {
  const [params, setParams] = useSearchParams();
  // Anything unfamiliar in the query falls back to morning rather than rendering an empty page.
  const tab: ReportTab = params.get("tab") === "eod" ? "eod" : "morning";

  const strip = (
    <TabStrip
      tabs={TABS}
      value={tab}
      labels={LABELS}
      ariaLabel="Report"
      // `replace` so flipping tabs does not stack history entries between a page and itself.
      onChange={(t) => { setParams(t === "morning" ? {} : { tab: t }, { replace: true }); }}
    />
  );

  // Keyed on the tab so the body fades on a switch — the shell's own fade keys on pathname, which
  // does not change here.
  return (
    <div key={tab} className="view-fade">
      {tab === "morning" ? <MorningPage tabs={strip} /> : <ReviewPage tabs={strip} />}
    </div>
  );
}
