/**
 * How the console names an account from manage's evaluation: the masked number on the main broker
 * login (`****1234`, as every page always showed it), `<login> ****1234` on another
 * (`broker.logins`). Two logins can hold accounts with the same last four, and every page keys an
 * account by this name -- React keys, the live marks matched to the evaluation, the bands history --
 * so the login has to be part of it. The orchestrator's posts name accounts the same way
 * (`managepost.account_ref`).
 */
export function accountName(a: Record<string, unknown>): string | null {
  const masked = typeof a["account"] === "string" ? a["account"] : null;
  if (masked === null) return null;
  const login = typeof a["login"] === "string" && a["login"] !== "" ? a["login"] : "main";
  return login === "main" ? masked : `${login} ${masked}`;
}
