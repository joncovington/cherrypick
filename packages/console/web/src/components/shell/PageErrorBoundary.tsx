import { Component, type ReactNode } from "react";

/**
 * Why a page went blank, and what to do about it.
 *
 * Every page's code is a lazily loaded chunk whose file name carries a content hash, and a build
 * deletes the previous build's chunks. A tab opened before a rebuild still runs the old app, so the
 * first visit to a page it had not loaded yet asks for a chunk that no longer exists -- the import
 * rejects, `React.lazy` throws it during render, and with no boundary anywhere React unmounts the
 * whole tree: a blank page that a refresh (which fetches the new build) cures. This was the
 * "occasionally a menu link opens a blank page" report on 2026-10-01, the day of many rebuilds.
 *
 * A stale-chunk failure reloads the page once, which is exactly the refresh that cured it by hand.
 * The guard below stops a build that genuinely lacks a chunk from reloading forever. Any other
 * render error is shown with its message rather than as an empty screen.
 */

const RELOAD_KEY = "cherrypick-chunk-reload-at";
/** Within this long of the last automatic reload, a second failure is a real fault, not staleness. */
const RELOAD_GUARD_MS = 15_000;

/** The messages browsers give a failed dynamic import (Chromium, Firefox, Safari), and Vite's own. */
export function isChunkLoadError(err: unknown): boolean {
  const text = err instanceof Error ? `${err.name} ${err.message}` : String(err);
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|ChunkLoadError|Unable to preload CSS/i.test(
    text,
  );
}

/** Reload once for a stale chunk; false when a reload already happened moments ago (the guard). */
export function reloadForStaleChunk(
  now: number = Date.now(),
  storage: Pick<Storage, "getItem" | "setItem"> | null = safeSession(),
  reload: () => void = () => window.location.reload(),
): boolean {
  const last = Number(storage?.getItem(RELOAD_KEY) ?? 0);
  if (Number.isFinite(last) && now - last < RELOAD_GUARD_MS) return false;
  storage?.setItem(RELOAD_KEY, String(now));
  reload();
  return true;
}

function safeSession(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

interface Props {
  /** Changes on navigation, so a page that failed does not keep the next one from rendering. */
  resetKey: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
  reloading: boolean;
}

export class PageErrorBoundary extends Component<Props, State> {
  state: State = { error: null, reloading: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error): void {
    if (isChunkLoadError(error) && reloadForStaleChunk()) this.setState({ reloading: true });
  }

  componentDidUpdate(prev: Props): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error !== null) {
      this.setState({ error: null, reloading: false });
    }
  }

  render(): ReactNode {
    const { error, reloading } = this.state;
    if (error === null) return this.props.children;
    if (reloading) return <p className="muted">This page was built after this tab opened — reloading…</p>;
    const stale = isChunkLoadError(error);
    return (
      <section className="card">
        <h2>{stale ? "This page could not be loaded" : "This page failed to render"}</h2>
        <p className={stale ? "muted" : "pnl-neg"}>{error.message}</p>
        {stale && (
          <p className="muted">
            The console was rebuilt after this tab opened and its code for this page is gone; a reload just
            happened and did not cure it, so the build itself may be incomplete.
          </p>
        )}
        <button type="button" className="btn" onClick={() => window.location.reload()}>
          Reload
        </button>
      </section>
    );
  }
}
