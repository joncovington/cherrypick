import { useQuery } from "@tanstack/react-query";
import type { LiveBook, LiveBooksPayload } from "@console/shared";
import { StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";
import { LiveFliesPanel } from "./LiveFliesPanel";

/**
 * The Overview's right column, generic over every module with a live book (2026-10-12; it had been
 * the flies pilot alone). `GET /api/live/books` lists each one with whether it has live activity
 * (`readers/liveBooks.ts`); only those that do are drawn, each by its own panel, and the rest are
 * named in one line so a quiet book is visibly quiet rather than missing.
 *
 * flies keeps its own panel (`LiveFliesPanel`, unchanged); bwb's is three tiles linking to its
 * live-vs-paper tab. A failed read is shown as that failure, never as "no activity".
 */

function useLiveBooks() {
  return useQuery<LiveBooksPayload>({
    queryKey: ["live-books"],
    queryFn: async () => {
      const res = await fetch("/api/live/books");
      if (!res.ok) throw new Error(`live books: HTTP ${String(res.status)}`);
      return (await res.json()) as LiveBooksPayload;
    },
    refetchInterval: 60_000,
  });
}

function tone(v: number | null): "pos" | "neg" | "dim" {
  if (v === null || v === 0) return "dim";
  return v > 0 ? "pos" : "neg";
}

function BwbLiveTiles({ book }: { book: LiveBook }) {
  return (
    <div className="grid-12">
      <StatTile
        label={`live bwb · ${book.latestSession?.slice(5) ?? "—"}`}
        value={String(book.open)}
        tone="dim"
        span={4}
        to={book.to}
        toLabel="the live arm against paper's"
        title="open live positions: established, not yet settled"
        foot={`${String(book.positions)} position${book.positions === 1 ? "" : "s"} held live`}
      />
      <StatTile
        label="last settled net"
        value={fmtMoney(book.latestNet)}
        tone={tone(book.latestNet)}
        span={4}
        to={book.to}
        title="gross − fees over the live positions that settled on that session"
        foot={book.latestNetSession ?? "nothing settled yet"}
      />
      <StatTile
        label="settled net · all"
        value={fmtMoney(book.totalNet)}
        tone={tone(book.totalNet)}
        span={4}
        to={book.to}
        title="gross − fees over every settled live position; costs are estimates until fee_reconcile replaces them"
      />
    </div>
  );
}

function quietLine(b: LiveBook): string {
  if (b.ledger === "absent") return `${b.module}: no live ledger on this machine`;
  if (b.ledger === "failed") return `${b.module}: the live ledger could not be read (${b.error ?? "unknown error"})`;
  return `${b.module}: no live activity yet`;
}

export function LivePanel() {
  const { data, isError } = useLiveBooks();
  if (isError) {
    return (
      <section className="card">
        <h2>live</h2>
        <p className="muted">The live books could not be read.</p>
      </section>
    );
  }
  if (data === undefined) {
    return <LiveFliesPanel />;
  }
  const active = data.books.filter((b) => b.ledger === "ok" && b.positions > 0);
  const quiet = data.books.filter((b) => !(b.ledger === "ok" && b.positions > 0));
  return (
    <div className="live-panel">
      {active.length === 0 && (
        <section className="card">
          <h2>live</h2>
          <p className="muted">No module has live activity on this machine yet.</p>
        </section>
      )}
      {active.map((b) => (b.module === "flies" ? <LiveFliesPanel key={b.module} /> : <BwbLiveTiles key={b.module} book={b} />))}
      {quiet.length > 0 && (
        <p className={quiet.some((b) => b.ledger === "failed") ? "chip chip-warn" : "muted"} style={{ fontSize: 11, margin: "0.4rem 0" }}>
          {quiet.map(quietLine).join(" · ")}
        </p>
      )}
    </div>
  );
}
