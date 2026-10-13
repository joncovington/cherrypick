// ------------------------------------------------------------------- live books (Overview)
// Every module with a live book and whether it has live activity, for the Overview's live panel
// (`server/src/readers/liveBooks.ts`, `GET /api/live/books`).

export interface LiveBook {
  module: "flies" | "bwb";
  /** `absent`: no live ledger on this machine; `failed`: the read threw (`error` says why). */
  ledger: "ok" | "absent" | "failed";
  error: string | null;
  /** The module's own page for its live book. */
  to: string;
  /** Positions ever held live (cancelled entries never held risk and are not counted). */
  positions: number;
  open: number;
  /** The newest session with a live position. */
  latestSession: string | null;
  /** Settled net of the newest session that settled anything, and that session. */
  latestNet: number | null;
  latestNetSession: string | null;
  /** Settled net over every settled live position. */
  totalNet: number | null;
}

export interface LiveBooksPayload {
  books: LiveBook[];
}
