import type { VersionPayload } from "@console/shared";
import { useVersion } from "../../lib/api";

const RELEASES_URL = "https://github.com/joncovington/cherrypick/releases/latest";
const HOW_TO_UPDATE =
  "To update: stop the suite (uninstall keeps history and settings), get the new release and run the installer again — INSTALL.md, \"Updating to a new release\". Read the release notes first.";

/** `v0.12.0`, or `v0.12.0+14` for a checkout 14 commits past the release. */
export function versionLabel(v: VersionPayload["installed"]): string {
  if (v.tag === null) return v.describe ?? "version unknown";
  return v.commitsPast !== null && v.commitsPast > 0 ? `${v.tag}+${String(v.commitsPast)}` : v.tag;
}

function checkLine(c: VersionPayload["check"]): string {
  if (!c.enabled) return "The release check is off (update_check: false in console.json).";
  if (c.error !== null) return `The release check failed: ${c.error}.`;
  if (c.latestTag === null) return "Checking GitHub for the latest release…";
  return `Latest release: ${c.latestName ?? c.latestTag}${c.latestPublishedAt ? `, published ${c.latestPublishedAt.slice(0, 10)}` : ""}.`;
}

/**
 * The suite version, at the end of the status bar on every page, and the one place a new release
 * is announced: amber, and a link to its notes, while GitHub's latest release is newer than the
 * tag this checkout is at or past. Amber, never red: an old version keeps working.
 */
export function VersionChip() {
  const data = useVersion().data;
  if (data === undefined) return null;
  const label = versionLabel(data.installed);
  const from = data.installed.source === "archive" ? " (from the release ZIP)" : data.installed.source === "git" ? " (git describe)" : "";
  const installedLine = `Installed: ${data.installed.describe ?? "unknown"}${from}.`;
  if (data.updateAvailable && data.check.latestTag !== null) {
    return (
      <a
        className="chip chip-warn"
        href={data.check.latestUrl ?? RELEASES_URL}
        target="_blank"
        rel="noreferrer"
        title={`${installedLine} ${checkLine(data.check)} ${HOW_TO_UPDATE}`}
      >
        {label} · {data.check.latestTag} available
      </a>
    );
  }
  return (
    <span className="chip" title={`${installedLine} ${checkLine(data.check)}`}>
      {label}
    </span>
  );
}
