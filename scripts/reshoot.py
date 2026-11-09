#!/usr/bin/env python3
"""Regenerate the guide's console screenshots from a demo home, and say which ones changed.

    python scripts/reshoot.py [--only live-today.png ...] [--day YYYY-MM-DD] [--use-running]
                              [--threshold 0.02] [--dry-run]

Every image in docs/images/shots.json is shot again: a fresh demo home is built with the manifest's
seed (scripts/demo_home.py), served through the demo launcher's own guards (scripts/demo_console.py:
never port 5070, never a real home, keyring off on both sides), and each entry's route (and card) is
captured with the console's ui-check. A shot is compressed to a palette PNG (Pillow) so it stays under
the guide's 150 KB limit, and replaces the committed image only when it differs by more than the
threshold (the share of pixels that changed). The table at the end lists what changed, so a person
can look at those before committing.

A step of docs/releasing.md before each release. The console must be built first (pnpm build in
packages/console). `--use-running` shoots a demo console already serving on the demo port instead
of building and serving one (the home it serves must be the manifest's seed).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "docs" / "images" / "shots.json"
IMAGES = REPO / "docs" / "images"
CONSOLE = REPO / "packages" / "console"
MAX_BYTES = 150 * 1024

sys.path.insert(0, str(REPO / "scripts"))

import demo_console  # noqa: E402  (its guards and environment, not a second copy of them)
import demo_home  # noqa: E402


def latest_trading_day(today: date) -> date:
    """The demo home's newest session: today if it is a trading day, else the one before."""
    return demo_home._trading_days(today, 1)[0]


def compress(png: bytes) -> bytes:
    """A palette PNG under the size limit: 256 colours, then fewer until it fits."""
    from PIL import Image

    img = Image.open(io.BytesIO(png)).convert("RGB")
    for colours in (256, 128, 64, 32):
        out = io.BytesIO()
        img.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
            out, format="PNG", optimize=True
        )
        if out.tell() <= MAX_BYTES:
            return out.getvalue()
    raise RuntimeError(f"cannot get under {MAX_BYTES // 1024} KB even at 32 colours: crop it (--card)")


def changed_share(old: bytes, new: bytes) -> float:
    """The share of pixels that differ; 1.0 when the sizes differ."""
    from PIL import Image, ImageChops

    a = Image.open(io.BytesIO(old)).convert("RGB")
    b = Image.open(io.BytesIO(new)).convert("RGB")
    if a.size != b.size:
        return 1.0
    diff = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 8 else 0)
    hist = diff.histogram()
    return hist[255] / (a.size[0] * a.size[1])


def wait_for(port: int, seconds: float = 90) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"the demo console did not come up on port {port}")


def serve(home: Path) -> tuple[int, list[subprocess.Popen]]:
    """Start the demo server (and its keepalive) under the launcher's guards; return the port."""
    port = demo_console.check(home, env_home=os.environ.get("CHERRYPICK_HOME"))
    if not demo_console.SERVER.is_file():
        raise SystemExit("the console server is not built: pnpm --dir packages/console build")
    env = demo_console.demo_env(dict(os.environ), home)
    children = [
        subprocess.Popen(
            [sys.executable, str(REPO / "scripts" / "demo_home.py"), "--out", str(home), "--keepalive"],
            env=env,
        ),
        subprocess.Popen(["node", str(demo_console.SERVER)], env=env, cwd=demo_console.SERVER.parent),
    ]
    return port, children


def stop(children: list[subprocess.Popen]) -> None:
    for c in children:
        if c.poll() is None:
            c.terminate()
    for c in children:
        try:
            c.wait(timeout=10)
        except subprocess.TimeoutExpired:
            c.kill()


def shoot(entry: dict, home: Path, out: Path) -> None:
    route = entry["route"]
    cmd = ["node", "tools/ui-check.mjs", "--route", route, "--viewport", entry.get("viewport", "1500x1000")]
    if entry.get("card"):
        cmd += ["--card", entry["card"]]
    cmd += ["--shot", str(out)]
    env = {**os.environ, "CHERRYPICK_HOME": str(home), "MSYS_NO_PATHCONV": "1"}
    r = subprocess.run(cmd, cwd=CONSOLE, env=env, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.is_file():
        raise RuntimeError(f"ui-check failed for {entry['file']}:\n{r.stdout[-800:]}\n{r.stderr[-800:]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=None, help="shoot only these image files")
    ap.add_argument("--day", default=None, help="the demo's latest session (default: the latest trading day)")
    ap.add_argument("--home", default=None, help="where to build the demo home (default: a temp folder)")
    ap.add_argument("--use-running", action="store_true", help="shoot the demo console already serving")
    ap.add_argument("--threshold", type=float, default=0.02, help="share of pixels that must change (0.02)")
    ap.add_argument("--dry-run", action="store_true", help="shoot and compare, but write nothing")
    args = ap.parse_args(argv)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = [e for e in manifest["shots"] if args.only is None or e["file"] in args.only]
    if args.only:
        missing = set(args.only) - {e["file"] for e in entries}
        if missing:
            raise SystemExit(f"not in shots.json: {', '.join(sorted(missing))}")

    demo = manifest["demo"]
    day = args.day or latest_trading_day(datetime.now().date()).isoformat()
    tmp = Path(tempfile.mkdtemp(prefix="reshoot-"))
    home = Path(args.home).resolve() if args.home else tmp / "demo-home"
    children: list[subprocess.Popen] = []
    rows: list[tuple[str, str]] = []
    try:
        if not args.use_running:
            print(f"building the demo home (seed {demo['seed']}, day {day}) in {home}", flush=True)
            subprocess.run(
                [
                    sys.executable,
                    str(REPO / "scripts" / "demo_home.py"),
                    "--out",
                    str(home),
                    "--seed",
                    str(demo["seed"]),
                    "--day",
                    day,
                    "--days",
                    str(demo.get("days", 30)),
                ],
                check=True,
            )
            port, children = serve(home)
            wait_for(port)
        elif args.home is None:
            raise SystemExit("--use-running needs --home: the home the running demo console serves")
        else:
            wait_for(demo_console.check(home, env_home=os.environ.get("CHERRYPICK_HOME")), 10)
        for e in entries:
            raw = tmp / f"raw-{e['file']}"
            try:
                shoot(e, home, raw)
                new = compress(raw.read_bytes())
            except (RuntimeError, subprocess.TimeoutExpired) as err:
                rows.append((e["file"], f"FAILED: {str(err).splitlines()[0]}"))
                continue
            target = IMAGES / e["file"]
            if not target.is_file():
                verdict = f"new ({len(new) // 1024} KB)"
            else:
                share = changed_share(target.read_bytes(), new)
                if share <= args.threshold:
                    rows.append((e["file"], f"unchanged ({share:.1%} of pixels)"))
                    continue
                verdict = f"CHANGED ({share:.1%} of pixels, {len(new) // 1024} KB)"
            if not args.dry_run:
                target.write_bytes(new)
            rows.append((e["file"], verdict + (" [dry run: not written]" if args.dry_run else "")))
    finally:
        stop(children)
        shutil.rmtree(tmp, ignore_errors=True)

    width = max((len(f) for f, _ in rows), default=10)
    for f, v in rows:
        print(f"{f.ljust(width)}  {v}")
    changed = [f for f, v in rows if v.startswith(("CHANGED", "new"))]
    if changed:
        print(f"\n{len(changed)} image(s) to look at before committing: {', '.join(changed)}")
    return 1 if any(v.startswith("FAILED") for _, v in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
