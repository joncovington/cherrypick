import type { ManageAccount } from "@console/shared";

/** The account as a person reads it: its name (`****1234`, or `margin ****4222` on another broker
 *  login) with that login's label beside it when one is set, and `managed` for an account on a login
 *  traded by hand. The name alone stays the key every page matches on; this is for display only. */
export function accountTitle(a: Pick<ManageAccount, "account" | "loginLabel" | "role">): string {
  const name = a.account ?? "?";
  const label = a.loginLabel ? ` (${a.loginLabel})` : "";
  return `${name}${label}${a.role === "managed" ? " · managed" : ""}`;
}
