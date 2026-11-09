#!/usr/bin/env python
"""Re-take every documentation screenshot of the console from the demo home, and say which changed.

    python scripts/reshoot.py [--write] [--threshold PERCENT] [--only NAME ...] [--home DIR]

Reads docs/images/shots.json (what each image shows, and the demo seed and day it was taken on),
rebuilds the fake home (scripts/demo_home.py), serves it through scripts/demo_console.py on a free port
(never 5070, never a real console's), takes each shot with packages/console/tools/ui-check.mjs,
compresses it and compares it with the image in the repository. The report lists each image as
unchanged, changed (more than THRESHOLD percent of its pixels differ) or new.

Nothing in the repository is touched without --write: the new images go to a temporary folder, named in
the last line. With --write they replace the old ones in place, ready to review and commit.

Needs the console built (docs/demo-home.md), Chrome or Edge, and Pillow (`pip install pillow`).
pngquant is used for compression when it is on the PATH, Pillow's own palette reduction otherwise.
It stops the server it started, by its own process id, and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs" / "images" / "shots.json"
CONSOLE = REPO / "packages" / "console"
MARKER = ".demo-home"
REAL_PORT = 5070
DEFAULT_VIEWPORT = "1500x1000"
TALL_VIEWPORT = "1500x2600"  # a page the app does not scroll, so a whole tab is in view
TAB_SELECTOR = ".mf-slide"  # one tab's content, without the rail, header and footer
DEFAULT_THRESHOLD = 0.5  # percent of pixels that must differ before an image is called changed
TOLERANCE = 24  # per-channel difference ignored: palette reduction moves a pixel a little


class Refused(SystemExit):
    """A guard said no; the message is the reason."""


# ---------------------------------------------------------------------------------- the manifest
def load_shots(path: Path = MANIFEST) -> list[dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["shots"] if isinstance(doc, dict) else doc


def groups(shots: list[dict]) -> dict[tuple[int, str], list[dict]]:
    """The shots by the demo they are taken on: one home is built per (seed, day)."""
    out: dict[tuple[int, str], list[dict]] = {}
    for shot in shots:
        out.setdefault((int(shot["seed"]), str(shot["day"])), []).append(shot)
    return out


def ui_check_args(shot: dict, out: Path) -> list[str]:
    """The ui-check arguments that take one manifest entry. A card crops to that card; a full page is one
    tab's content in a viewport tall enough not to scroll; anything else is the viewport as it is."""
    args = ["--route", shot["route"], "--shot", str(out)]
    if shot.get("card"):
        args += ["--card", shot["card"]]
    elif shot.get("full_page"):
        args += ["--selector", shot.get("selector") or TAB_SELECTOR]
        args += ["--viewport", shot.get("viewport") or TALL_VIEWPORT]
    else:
        if shot.get("selector"):
            args += ["--selector", shot["selector"]]
        args += ["--viewport", shot.get("viewport") or DEFAULT_VIEWPORT]
    scale = shot.get("scale", 1)
    if scale != 1:
        args += ["--scale", str(scale)]
    return args


# ---------------------------------------------------------------------------------- the home
def check_home(home: Path, real: Path | None = None) -> None:
    """Refuse a folder the build would empty that is not a demo home: the build deletes what is in it."""
    real = (real if real is not None else Path.home() / ".cherrypick").resolve()
    home = home.resolve()
    if home == real or real in home.parents or home in real.parents:
        raise Refused(f"refusing: {home} is, or contains, the real home")
    if home.exists() and any(home.iterdir()) and not (home / MARKER).is_file():
        raise Refused(f"refusing: {home} is not empty and has no {MARKER} marker; it would be emptied")


def free_port(avoid: set[int]) -> int:
    for _ in range(50):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in avoid:
            return port
    raise Refused("no free port found")


def real_console_port() -> int | None:
    try:
        doc = json.loads(
            (Path.home() / ".cherrypick" / "config" / "console.json").read_text(encoding="utf-8")
        )
        port = doc["serve"]["port"]
        return int(port) if isinstance(port, int) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_home(home: Path, seed: int, day: str, port: int) -> None:
    done = subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "demo_home.py"),
            "--out",
            str(home),
            "--seed",
            str(seed),
            "--day",
            day,
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if done.returncode != 0:
        raise SystemExit(f"demo_home.py failed:\n{done.stderr[-1500:]}")
    cfg = home / "config" / "console.json"
    cfg.write_text(json.dumps({"serve": {"port": port}}, indent=2) + "\n", encoding="utf-8")


def stop(proc: subprocess.Popen) -> None:
    """End the server this script started and its children, by its own process id."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def serve(home: Path, port: int) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != "CHERRYPICK_HOME"}
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "scripts" / "demo_console.py"), "--home", str(home), "--keepalive"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit("demo_console.py exited early (is the console built? see docs/demo-home.md)")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
                if r.status == 200:
                    time.sleep(8)  # the keepalive writes its first heartbeats
                    return proc
        except OSError:
            time.sleep(1)
    stop(proc)
    raise SystemExit("the demo console did not come up in 90 seconds")


# ---------------------------------------------------------------------------------- one shot
def take(shot: dict, home: Path, port: int, out: Path) -> None:
    """Run ui-check for one entry against this home; fail if it did not talk to our port."""
    env = {k: v for k, v in os.environ.items()}
    env["CHERRYPICK_HOME"] = str(home)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    done = subprocess.run(
        ["node", "tools/ui-check.mjs", *ui_check_args(shot, out)],
        cwd=CONSOLE,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    seen = re.search(r"http://127\.0\.0\.1:(\d+)/", done.stdout + done.stderr)
    if seen is None or int(seen.group(1)) != port:
        raise SystemExit(
            f"{shot['path']}: ui-check did not use the demo port {port} (saw {seen and seen.group(1)})"
        )
    if done.returncode != 0 or not out.is_file():
        raise SystemExit(f"{shot['path']}: ui-check failed:\n{(done.stdout + done.stderr)[-800:]}")
    finish(out, shot)


def finish(path: Path, shot: dict) -> None:
    """Crop to the entry's box, then compress."""
    from PIL import Image

    im = Image.open(path)
    box = shot.get("crop")
    if box:
        scale = shot.get("scale", 1)
        im = im.crop(tuple(int(v * scale) for v in box))
    im.save(path)
    compress(path)


def compress(path: Path) -> None:
    """pngquant when present, else Pillow's palette reduction and an optimised PNG."""
    exe = shutil.which("pngquant")
    if exe:
        subprocess.run(
            [exe, "--force", "--quality=70-95", "--output", str(path), str(path)], capture_output=True
        )
        return
    from PIL import Image

    im = Image.open(path).convert("RGB")
    im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
        path, optimize=True
    )


def changed_fraction(old: Path, new: Path) -> float:
    """The share of pixels (0 to 1) that differ by more than TOLERANCE in a channel; 1.0 for another size."""
    from PIL import Image, ImageChops

    a, b = Image.open(old).convert("RGB"), Image.open(new).convert("RGB")
    if a.size != b.size:
        return 1.0
    red, green, blue = ImageChops.difference(a, b).split()
    worst = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    mask = worst.point(lambda v: 255 if v > TOLERANCE else 0)
    return mask.histogram()[255] / (a.size[0] * a.size[1])


def verdict(old: Path, new: Path, threshold_percent: float) -> tuple[str, float]:
    if not old.is_file():
        return "new", 1.0
    frac = changed_fraction(old, new)
    return ("changed" if frac * 100 > threshold_percent else "unchanged"), frac


# ---------------------------------------------------------------------------------- the run
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--write", action="store_true", help="replace the images in the repository (default: a temp folder)"
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="percent of pixels that must differ (default %(default)s)",
    )
    ap.add_argument(
        "--only", nargs="*", default=[], help="take only these images (a name or a path fragment)"
    )
    ap.add_argument("--home", help="where to build the demo home (default: a new temp folder)")
    ap.add_argument("--out", help="where new images go without --write (default: a new temp folder)")
    ap.add_argument("--manifest", default=str(MANIFEST))
    args = ap.parse_args(argv)

    shots = [
        s for s in load_shots(Path(args.manifest)) if not args.only or any(o in s["path"] for o in args.only)
    ]
    if not shots:
        raise SystemExit("no shot in the manifest matches")
    home_root = Path(args.home) if args.home else Path(tempfile.mkdtemp(prefix="cherrypick-demo-"))
    check_home(home_root)
    staging = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="cherrypick-shots-"))
    avoid = {REAL_PORT, real_console_port() or REAL_PORT}
    rows: list[tuple[str, str, float]] = []
    try:
        for (seed, day), batch in groups(shots).items():
            port = free_port(avoid)
            print(f"demo seed {seed} day {day}: building the home and serving on port {port}", flush=True)
            build_home(home_root, seed, day, port)
            proc = serve(home_root, port)
            try:
                for shot in batch:
                    new = staging / Path(shot["path"]).name
                    take(shot, home_root, port, new)
                    state, frac = verdict(REPO / shot["path"], new, args.threshold)
                    rows.append((shot["path"], state, frac))
                    print(f"  {state:9} {frac * 100:6.2f}%  {shot['path']}", flush=True)
            finally:
                stop(proc)
    finally:
        if not args.home:
            shutil.rmtree(home_root, ignore_errors=True)  # a home this run made, and only that
    changed = [r for r in rows if r[1] != "unchanged"]
    print(
        f"\n{len(rows)} image(s) taken, {len(changed)} changed or new (threshold {args.threshold}% of pixels)"
    )
    if args.write:
        for path, _state, _frac in changed:
            (REPO / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staging / Path(path).name, REPO / path)
        print(f"wrote {len(changed)} image(s) in place; review them, then commit")
    else:
        print(f"nothing written to the repository; the new images are in {staging}")
        print("run with --write to replace the repository's images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
