import { loadCredentials, clearLegacySlots, mask, resetCredentialCache } from "../auth/credentials.js";
import { probeCredentials } from "../auth/probe.js";

const SETUP_CMD = "python -m cherrypick.core.auth setup";

async function main(): Promise<number> {
  const action = process.argv[2];

  switch (action) {
    case "set": {
      // Deliberately not a writer: the suite has exactly ONE path for setting
      // broker credentials, and it is the shared onboarding CLI. Two writers
      // is how credential stores drift.
      console.error(
        "The console does not write credentials — there is one path for the\n" +
          `whole suite:\n\n    ${SETUP_CMD}\n\n` +
          "That writes the shared entries every module (and this console) reads.\n" +
          "After rotating, run `credentials probe` here to re-detect scope.",
      );
      return 2;
    }
    case "probe": {
      const creds = loadCredentials();
      if (creds === null) {
        console.error(`No suite credential found. Set one with: ${SETUP_CMD}`);
        return 1;
      }
      console.log("Validating against the broker…");
      const probe = await probeCredentials(creds);
      if (!probe.ok) {
        console.error(`Validation failed: ${probe.error}`);
        return 1;
      }
      console.log(`OK. Account ${probe.account}, detected scope: ${probe.scope}.`);
      if (probe.scope === "read") {
        console.log(
          "\nWARNING: this refresh token is READ-ONLY — write-oriented functions\n" +
            "(broker dry-run validation of staged tickets) stay disabled. Rotate in\n" +
            `a trade-scoped refresh token via: ${SETUP_CMD}`,
        );
      }
      return 0;
    }
    case "show": {
      const creds = loadCredentials();
      if (creds === null) {
        console.log(`No suite credential found. Set one with: ${SETUP_CMD}`);
        return 0;
      }
      console.log(`source: ${creds.source === "suite" ? "suite entries (cherrypick-broker, production:*)" : "legacy console slot"}`);
      console.log(`client secret: ${mask(creds.clientSecret)}`);
      console.log(`refresh token: ${mask(creds.refreshToken)}`);
      if (creds.source === "legacy-slot") {
        console.log(`note: the suite entries are absent — populate the single source with: ${SETUP_CMD}`);
      }
      return 0;
    }
    case "clear": {
      const cleared = clearLegacySlots();
      resetCredentialCache();
      console.log(cleared ? "Legacy console slots cleared." : "No legacy console slots to clear.");
      console.log(`The suite entries are managed only by: ${SETUP_CMD}`);
      return 0;
    }
    default:
      console.error("usage: credentials-cli <show|probe|clear>  (set is handled suite-wide — see `set` for the pointer)");
      return 2;
  }
}

process.exit(await main());
