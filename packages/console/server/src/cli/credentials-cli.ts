import readline from "node:readline";
import { Writable } from "node:stream";
import {
  saveCredentials,
  loadCredentials,
  clearCredentials,
  mask,
} from "../auth/credentials.js";
import { probeCredentials } from "../auth/probe.js";

function promptHidden(question: string): Promise<string> {
  // Echo-suppressing prompt: readline writes to a sink while the secret is typed.
  let muted = false;
  const sink = new Writable({
    write(chunk: Buffer | string, _enc, cb) {
      if (!muted) process.stdout.write(chunk);
      cb();
    },
  });
  const rl = readline.createInterface({ input: process.stdin, output: sink, terminal: true });
  return new Promise((resolve) => {
    process.stdout.write(question);
    muted = true;
    rl.question("", (answer) => {
      muted = false;
      rl.close();
      process.stdout.write("\n");
      resolve(answer.trim());
    });
  });
}

async function main(): Promise<number> {
  const action = process.argv[2];

  switch (action) {
    case "set": {
      console.log(
        "Storing THE suite broker credential (one credential set for the whole\n" +
          "suite — Windows Credential Manager, service \"cherrypick-broker\", slot\n" +
          "\"oauth\"): the OAuth application's shared client secret plus a refresh\n" +
          "token. Scope rides on the refresh token.\n",
      );
      const clientSecret = await promptHidden("Client secret (shared): ");
      const refreshToken = await promptHidden("Refresh token: ");
      if (clientSecret === "" || refreshToken === "") {
        console.error("Both values are required — nothing saved.");
        return 1;
      }
      console.log("Validating against the broker…");
      const probe = await probeCredentials({ clientSecret, refreshToken });
      if (!probe.ok) {
        console.error(`Validation failed: ${probe.error}`);
        console.error("Nothing saved — check the values and try again.");
        return 1;
      }
      saveCredentials({ clientSecret, refreshToken, scope: probe.scope, validatedAt: new Date().toISOString() });
      console.log(`Saved. Account ${probe.account}, detected scope: ${probe.scope}.`);
      if (probe.scope === "read") {
        console.log(
          "\nWARNING: this refresh token is READ-ONLY. Write-oriented functions are\n" +
            "disabled across the console: staged tickets will save WITHOUT broker\n" +
            "dry-run validation. Generate a trade-scoped refresh token and re-run\n" +
            "`credentials set` to enable dry-run validation (the console still can\n" +
            "never place an order — that invariant is enforced in CI regardless).",
        );
      }
      return 0;
    }
    case "show": {
      const creds = loadCredentials();
      if (creds === null) {
        console.log("No suite credential stored.");
        return 0;
      }
      console.log(`client secret: ${mask(creds.clientSecret)}`);
      console.log(`refresh token: ${mask(creds.refreshToken)}`);
      console.log(`scope: ${creds.scope ?? "unknown (never validated)"}${creds.validatedAt !== undefined ? ` — validated ${creds.validatedAt}` : ""}`);
      return 0;
    }
    case "clear": {
      console.log(clearCredentials() ? "Credential cleared." : "No suite credential stored.");
      return 0;
    }
    default:
      console.error("usage: credentials-cli <set|show|clear>");
      return 2;
  }
}

process.exit(await main());
