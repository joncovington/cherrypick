/**
 * Why Electron's sandbox will not start on this Linux host, before it crashes cryptically -- or null.
 *
 * Ubuntu 23.10+ (and 24.04) restrict unprivileged user namespaces through AppArmor, and Electron run
 * from node_modules then needs its `chrome-sandbox` helper owned by root with the setuid bit; without
 * it the window dies with "The SUID sandbox helper binary was found, but is not configured
 * correctly" (2026-10-08 OS audit). This names the one-time fix instead. It never turns the sandbox
 * off: `--no-sandbox` would trade a clear message for a quieter, weaker window.
 *
 * Pure over its inputs so it is tested without Linux: `readFile(path)` returns text or throws,
 * `stat(path)` returns {uid, mode} or throws.
 */
import path from "node:path";

export function sandboxProblem({ platform, electronBinary, readFile, stat }) {
  if (platform !== "linux") return null;
  let restricted;
  try {
    restricted = readFile("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").trim() === "1";
  } catch {
    return null; // no such switch: user namespaces are not restricted this way
  }
  if (!restricted) return null;
  const helper = path.posix.join(path.posix.dirname(electronBinary), "chrome-sandbox"); // Linux-only path
  let st;
  try {
    st = stat(helper);
  } catch {
    return null; // a build without the helper; let Electron speak for itself
  }
  if (st.uid === 0 && (st.mode & 0o4000) !== 0) return null;
  return [
    "This Linux restricts the sandbox the console window runs in (AppArmor), and Electron's helper is",
    "not set up for it. Fix it once with:",
    "",
    `  sudo chown root:root "${helper}" && sudo chmod 4755 "${helper}"`,
    "",
    "Or skip the window: the browser at http://127.0.0.1:5070 shows exactly the same console.",
    "(Reinstalling the console's packages undoes the fix; run the two commands again after that.)",
  ].join("\n");
}
