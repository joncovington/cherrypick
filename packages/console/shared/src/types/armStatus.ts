/** The modules whose console pages carry an arm filter. */
export type ArmModule = "flies" | "meic" | "bwb" | "curve" | "pmcc" | "contango";

/**
 * Arms each module's own config declares retired (`enabled: false`). The arm filters hide these by
 * default; a ledger keeps a retired arm's rows, so the dropdown would otherwise list every arm that
 * ever traded. An arm missing from a registry, or a synthetic book (`advised:*`), is never listed here.
 */
export interface RetiredArmsPayload {
  retired: Record<ArmModule, string[]>;
}
