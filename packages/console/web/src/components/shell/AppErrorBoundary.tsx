import { Component, type ReactNode } from "react";
import { isChunkLoadError, reloadForStaleChunk } from "./PageErrorBoundary";

/**
 * The last line before an empty screen.
 *
 * `PageErrorBoundary` covers the page pane only. The shell around it -- the header, the status bar,
 * the toasts, and the hooks the shell runs for every page (trade notifications, keyboard nav, prefs
 * sync) -- had no boundary, so a render error anywhere in it unmounted the whole tree and left a
 * blank console with nothing to say why. This wraps the app, so that failure shows its message and
 * a way back instead.
 *
 * A stale chunk reloads once here too, under the same guard, since a shell piece can be lazy as well.
 * It is deliberately plain: it renders when the app itself is what failed, so it leans on nothing
 * but the base stylesheet.
 */

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error): void {
    if (isChunkLoadError(error)) reloadForStaleChunk();
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    return (
      <section className="card" style={{ margin: 24 }}>
        <h2>The console failed to render</h2>
        <p className="pnl-neg">{error.message}</p>
        <p className="muted">
          This was outside any single page (the header, status bar or a shell hook), so the whole console
          stopped. Reloading starts it again; the browser's developer console has the stack.
        </p>
        <button type="button" className="btn" onClick={() => window.location.reload()}>
          Reload
        </button>
      </section>
    );
  }
}
