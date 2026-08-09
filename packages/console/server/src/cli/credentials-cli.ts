import readline from "node:readline";
import { Writable } from "node:stream";
import {
  saveCredentials,
  loadCredentials,
  clearCredentials,
  mask,
} from "../auth/credentials.js";

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
        "Storing the console's tastytrade OAuth credential (Windows Credential Manager,\n" +
          "service \"cherrypick-console\"): the OAuth application's shared client secret\n" +
          "plus the console's own READ-ONLY refresh token (scope rides on the refresh\n" +
          "token — generate one at my.tastytrade.com → API → OAuth applications).\n",
      );
      const clientSecret = await promptHidden("Client secret (shared): ");
      const refreshToken = await promptHidden("Read-only refresh token: ");
      if (clientSecret === "" || refreshToken === "") {
        console.error("Both values are required — nothing saved.");
        return 1;
      }
      saveCredentials({ clientSecret, refreshToken });
      console.log("Saved. The console will use this grant for market data and reads only.");
      return 0;
    }
    case "show": {
      const creds = loadCredentials();
      if (creds === null) {
        console.log("No console credential stored.");
        return 0;
      }
      console.log(`client secret: ${mask(creds.clientSecret)}`);
      console.log(`refresh token: ${mask(creds.refreshToken)}`);
      return 0;
    }
    case "clear": {
      console.log(clearCredentials() ? "Credential cleared." : "No console credential stored.");
      return 0;
    }
    default:
      console.error("usage: credentials-cli <set|show|clear>");
      return 2;
  }
}

process.exit(await main());
