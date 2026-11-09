/**
 * `/api/version`: the suite version this checkout holds, and whether GitHub has a newer release.
 *
 * `installed` comes from `git describe` in a git checkout, or from `.git_archival.txt` (filled in
 * by GitHub when it builds a release ZIP). `latest` is the newest published release, asked of the
 * GitHub API at most every few hours, and only while `update_check` in console.json is not false.
 */
export interface VersionPayload {
  installed: {
    /** As `git describe` prints it: `v0.12.0` on a release, `v0.12.0-14-gabc1234` past one. */
    describe: string | null;
    /** The release tag the checkout is at or past (`v0.12.0`); null when none can be found. */
    tag: string | null;
    /** Commits past `tag`; 0 on the release itself, null when unknown. */
    commitsPast: number | null;
    /** Where the version was read: a git checkout, a release ZIP, or nowhere. */
    source: "git" | "archive" | null;
  };
  check: {
    /** False when `update_check: false` in console.json turns the GitHub request off. */
    enabled: boolean;
    /** The newest published release's tag, or null before the first answer or on failure. */
    latestTag: string | null;
    latestName: string | null;
    latestUrl: string | null;
    latestPublishedAt: string | null;
    /** When GitHub last answered (or failed to). */
    checkedAt: string | null;
    error: string | null;
  };
  /** True only when both tags are known and the latest release is newer than `installed.tag`. */
  updateAvailable: boolean;
}
