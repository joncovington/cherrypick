/**
 * Which modules each shared reader serves, declared once for the server and the web. Each side kept
 * its own copy until 2026-10-06, and the web's attempts list had fallen a module behind the
 * server's. `server/test/module-tables.test.ts` checks every decisions/attempts table a module's
 * ledger declares is read somewhere, so a table nobody reads fails there rather than going unseen.
 */

/** Modules whose `*_entry_attempts` table the shared attempts reader (`readers/attempts.ts`) reads. */
export type AttemptsModule = "meic" | "flies" | "pmcc" | "curve";

/** Modules whose `*_decisions` journal the shared decisions reader (`readers/decisions.ts`) reads. */
export type DecisionsModule = "curve" | "pmcc" | "bwb" | "contango";
