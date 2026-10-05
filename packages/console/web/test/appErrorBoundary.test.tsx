import fs from "node:fs";
import path from "node:path";
import { describe, it, expect } from "vitest";
import { renderToString } from "react-dom/server";
import { AppErrorBoundary } from "../src/components/shell/AppErrorBoundary";

/**
 * A render error in the shell (header, status bar, a shell hook) used to unmount the whole tree:
 * a blank console with no message. The app-level boundary turns it into a message and a reload.
 *
 * The inline background in index.html exists to tell a blank app (dark) from an unpainted tab
 * (white), which only works while it is the theme's colour -- so it is checked against styles.css.
 */

const web = path.resolve(__dirname, "..");

describe("app error boundary", () => {
  it("renders its children while nothing has failed", () => {
    const b = new AppErrorBoundary({ children: <p>shell</p> });
    expect(renderToString(<>{b.render()}</>)).toContain("shell");
  });

  it("a shell error becomes a message, not an empty tree", () => {
    const b = new AppErrorBoundary({ children: <p>shell</p> });
    b.state = AppErrorBoundary.getDerivedStateFromError(new Error("prefs mirror is not an object"));
    const html = renderToString(<>{b.render()}</>);
    expect(html).toContain("The console failed to render");
    expect(html).toContain("prefs mirror is not an object");
    expect(html).not.toContain("shell</p>");
  });

  it("wraps the whole app in main.tsx, outside the providers and router", () => {
    const main = fs.readFileSync(path.join(web, "src", "main.tsx"), "utf8");
    const boundary = main.indexOf("<AppErrorBoundary>");
    expect(boundary).toBeGreaterThan(-1);
    expect(boundary).toBeLessThan(main.indexOf("<QueryClientProvider"));
    expect(main.indexOf("</AppErrorBoundary>")).toBeGreaterThan(main.indexOf("</QueryClientProvider>"));
  });
});

describe("pre-stylesheet background", () => {
  it("index.html paints the theme's --bg before styles.css loads", () => {
    const css = fs.readFileSync(path.join(web, "src", "styles.css"), "utf8");
    const html = fs.readFileSync(path.join(web, "index.html"), "utf8");
    const bg = /--bg:\s*(#[0-9a-f]{3,8})\s*;/i.exec(css)?.[1];
    expect(bg).toBeDefined();
    expect(html.toLowerCase()).toContain(`background: ${bg!.toLowerCase()};`);
  });
});
