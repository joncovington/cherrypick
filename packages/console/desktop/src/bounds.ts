/**
 * Remembered window geometry, in the console's own store (`data/console/`) — the one directory this
 * package is allowed to write. Deliberately tolerant: a corrupt or stale file means "use the
 * default", never a shell that will not open.
 */
import fs from "node:fs";
import path from "node:path";
import { cherrypickHome } from "@console/shared";

export interface Bounds {
  x?: number;
  y?: number;
  width: number;
  height: number;
  maximized?: boolean;
}

export const DEFAULT_BOUNDS: Bounds = { width: 1440, height: 900 };
/** Below this the console's cards stop laying out sensibly, so a saved sliver is discarded. */
const MIN_WIDTH = 800;
const MIN_HEIGHT = 600;

export function boundsPath(home = cherrypickHome()): string {
  return path.join(home, "data", "console", "desktop-window.json");
}

export function loadBounds(home?: string): Bounds {
  try {
    const raw = JSON.parse(fs.readFileSync(boundsPath(home), "utf-8")) as Partial<Bounds>;
    const width = Number(raw.width);
    const height = Number(raw.height);
    if (!Number.isFinite(width) || !Number.isFinite(height)) return DEFAULT_BOUNDS;
    if (width < MIN_WIDTH || height < MIN_HEIGHT) return DEFAULT_BOUNDS;
    const out: Bounds = { width, height, maximized: raw.maximized === true };
    // A monitor that has been unplugged leaves coordinates pointing off-screen; Electron would put
    // the window somewhere invisible. Dropping x/y re-centres it, which is the recoverable choice.
    if (Number.isFinite(Number(raw.x)) && Number.isFinite(Number(raw.y))) {
      out.x = Number(raw.x);
      out.y = Number(raw.y);
    }
    return out;
  } catch {
    return DEFAULT_BOUNDS;
  }
}

export function saveBounds(bounds: Bounds, home?: string): void {
  try {
    const file = boundsPath(home);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(bounds, null, 2) + "\n", "utf-8");
  } catch {
    // Losing the remembered size is not worth a failed quit.
  }
}
