import { describe, it, expect } from "vitest";
import { accountName } from "../src/readers/accountName.js";

/** An account on another broker login is named with its login, so two logins' ****1234 never meet. */
describe("accountName", () => {
  it("keeps the main login's masked number exactly as before", () => {
    expect(accountName({ account: "****1234" })).toBe("****1234");
    expect(accountName({ account: "****1234", login: "main" })).toBe("****1234");
  });

  it("names an extra login's account with its login", () => {
    expect(accountName({ account: "****1234", login: "margin" })).toBe("margin ****1234");
  });

  it("is null with no account", () => {
    expect(accountName({ login: "margin" })).toBeNull();
  });

  it("keeps two logins' equal last fours apart", () => {
    const names = [{ account: "****1234" }, { account: "****1234", login: "margin" }].map(accountName);
    expect(new Set(names).size).toBe(2);
  });
});
