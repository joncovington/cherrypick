/**
 * Launch the shell with `ELECTRON_RUN_AS_NODE` cleared.
 *
 * VS Code sets that variable in its integrated terminal (it reuses its own Electron binary to run
 * Node child processes), and it is inherited by anything started there. With it set, the Electron
 * binary runs as **plain Node**: no main process, `process.type` undefined, and `import ... from
 * "electron"` resolves to the npm package's shim — a path string — instead of the built-in module.
 * The failure looks like a bug in the app rather than the environment, which is why this exists
 * rather than a bare `electron .` in package.json.
 *
 * Portable on purpose: `env -u` is not available on Windows and a cross-env dependency is not worth
 * it for one variable.
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
// Outside a main process the electron package exports the path to its binary, which is exactly what
// is wanted here.
const electronBinary = require("electron");
const appDir = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

const env = { ...process.env };
delete env["ELECTRON_RUN_AS_NODE"];

const child = spawn(electronBinary, [appDir, ...process.argv.slice(2)], {
  env,
  stdio: "inherit",
  windowsHide: false,
});
child.on("exit", (code, signal) => process.exit(signal !== null ? 1 : (code ?? 0)));
child.on("error", (err) => {
  console.error(`failed to launch electron: ${err.message}`);
  process.exit(1);
});
