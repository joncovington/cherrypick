import { describe, it, expect, vi } from "vitest";
import { renderToString } from "react-dom/server";
import type { VersionPayload } from "@console/shared";

/**
 * The status bar's version chip: the plain version normally, and an amber link naming the new
 * release only when the server says one is out.
 */

let payload: VersionPayload | undefined;

vi.mock("../src/lib/api", () => ({
  useVersion: () => ({ data: payload }),
}));

const { VersionChip, versionLabel } = await import("../src/components/shell/VersionChip");

function version(over: Partial<VersionPayload> = {}): VersionPayload {
  return {
    installed: { describe: "v0.11.2", tag: "v0.11.2", commitsPast: 0, source: "archive" },
    check: {
      enabled: true,
      latestTag: "v0.11.2",
      latestName: "v0.11.2 — review follow-ups",
      latestUrl: "https://github.com/joncovington/cherrypick/releases/tag/v0.11.2",
      latestPublishedAt: "2026-10-19T06:45:32Z",
      checkedAt: "2026-11-09T00:00:00Z",
      error: null,
    },
    updateAvailable: false,
    ...over,
  };
}

function render(p: VersionPayload | undefined): string {
  payload = p;
  return renderToString(<VersionChip />).replace(/<!-- -->/g, "");
}

describe("versionLabel", () => {
  it("shows the release, commits past it, or the raw describe", () => {
    expect(versionLabel({ describe: "v0.12.0", tag: "v0.12.0", commitsPast: 0, source: "git" })).toBe("v0.12.0");
    expect(versionLabel({ describe: "v0.12.0-14-gabc1234", tag: "v0.12.0", commitsPast: 14, source: "git" })).toBe("v0.12.0+14");
    expect(versionLabel({ describe: null, tag: null, commitsPast: null, source: null })).toBe("version unknown");
  });
});

describe("VersionChip", () => {
  it("is a plain chip on the latest release", () => {
    const html = render(version());
    expect(html).toContain(">v0.11.2<");
    expect(html).not.toContain("available");
    expect(html).not.toContain("chip-warn");
  });

  it("is an amber link to the release when a newer one is out", () => {
    const html = render(
      version({
        updateAvailable: true,
        check: { ...version().check, latestTag: "v0.12.0", latestUrl: "https://github.com/joncovington/cherrypick/releases/tag/v0.12.0" },
      }),
    );
    expect(html).toContain("chip-warn");
    expect(html).toContain("v0.11.2 · v0.12.0 available");
    expect(html).toContain('href="https://github.com/joncovington/cherrypick/releases/tag/v0.12.0"');
    expect(html).toContain("Updating to a new release");
  });

  it("says the check is off when it is", () => {
    const html = render(version({ check: { ...version().check, enabled: false, latestTag: null } }));
    expect(html).toContain("update_check: false");
  });

  it("renders nothing before the first answer", () => {
    expect(render(undefined)).toBe("");
  });
});
