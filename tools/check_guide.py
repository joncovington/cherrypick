#!/usr/bin/env python3
r"""Keep the user guides (`docs/guide/`) honest, because the console's Help page serves them.

`check_docs.py` guards the whole repo's docs. These rules are specific to the guide, which has an
index, an FAQ with a fixed shape, images, and commands a reader will paste:

  1. The index matches the files. `docs/guide/README.md` is the Help page's table of contents and
     only listed pages are served, so an unlisted page is invisible and a listed-but-missing one is a
     404. Every item must be `- [Title](slug.md): description.` so the page can be built from it.
  2. Links and anchors resolve, in every guide page and the index, and in the console's `/help/<slug>`
     (`#anchor`) string literals. A renamed heading silently kills every link to it.
  3. Images: each one a guide page references exists; each file in `docs/images/` is referenced by
     some markdown file (an unused screenshot is dead weight in a public repo) and is at most
     150 KB (the repo is cloned by every user).
  4. The FAQ keeps its shape (`#` title, `##` groups each with `###` questions, each with a
     non-empty answer, no deeper headings, unique questions), since the Help page renders it as an
     accordion.
  5. A `run.py <verb>` in code names a real orchestrator CLI command.
  6. A `python -m cherrypick.<module>` in code names a module that exists.
  7. A `--flag` in a `run.py <verb> ...` command appears in the orchestrator CLI source. A flag
     that was renamed would otherwise send a reader at an argparse error.

Rules 5-7 look only inside inline code and fenced code, so prose cannot trip them. Config-key drift
(keys named in config.example.json) is not checked: the guides do not tie a key to a file name in
a form that can be matched without false positives.

Plain stdlib; run `python tools/check_guide.py`. Exits non-zero with one line per finding.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_docs as cd  # noqa: E402

ROOT = cd.ROOT
GUIDE = "docs/guide"
IMAGES = "docs/images"
WEB_SRC = "packages/console/web/src"
MAX_IMAGE_BYTES = 150 * 1024

_ITEM = re.compile(r"^- \[([^\]]+)\]\(([a-z0-9-]+\.md)\): (\S.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LINK = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_INLINE_CODE = re.compile(r"`+[^`\n]*`+")
_HELP_LITERAL = re.compile(r"""["'`]/help/([A-Za-z0-9_-]+)(?:#([A-Za-z0-9_-]+))?""")
_RUN_PY = re.compile(r"\brun\.py\s+([a-z][a-z-]*)((?:\s+[^\s`]+)*)")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
_DASH_M = re.compile(r"\bpython3?\s+-m\s+(cherrypick\.[\w.]+)")
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "__pycache__"}


def _md_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*.md") if not (_SKIP_DIRS & set(p.relative_to(root).parts))]


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8").split("\n")


def _prose(lines: list[str]) -> list[tuple[int, str]]:
    """(line number, text) for lines outside fences, with inline code blanked out."""
    out, fenced = [], False
    for n, line in enumerate(lines, 1):
        if cd._FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append((n, _INLINE_CODE.sub("", line)))
    return out


def _code_segments(lines: list[str]) -> list[tuple[int, str]]:
    """(line number, text) for each inline code span and each line inside a fence."""
    out, fenced = [], False
    for n, line in enumerate(lines, 1):
        if cd._FENCE.match(line):
            fenced = not fenced
        elif fenced:
            out.append((n, line))
        else:
            out += [(n, m.group(0).strip("`")) for m in _INLINE_CODE.finditer(line)]
    return out


def slugify(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Mirrors packages/console/shared/src/types/help.ts: trim, lowercase, keep letters, marks,
    # numbers, underscore, space and hyphen, then each space becomes a hyphen (no collapsing).
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    """GitHub-style heading anchors of a markdown file, with -1, -2 de-duplication."""
    seen: dict[str, int] = {}
    out: set[str] = set()
    fenced = False
    for line in _lines(path):
        if cd._FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        if not m:
            continue
        s = slugify(m.group(2))
        k = seen.get(s, 0)
        seen[s] = k + 1
        out.add(s if k == 0 else f"{s}-{k}")
    return out


def _index(root: Path) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Listed (line, title, slug) items and the findings for malformed items."""
    idx = root / GUIDE / "README.md"
    items, findings = [], []
    if not idx.is_file():
        return items, [f"{GUIDE}/README.md:1: rule 1: the index is missing"]
    for n, line in enumerate(_lines(idx), 1):
        if not line.startswith("- "):
            continue
        m = _ITEM.match(line)
        if not m:
            findings.append(
                f"{GUIDE}/README.md:{n}: rule 1: index item is not `- [Title](slug.md): description.`"
            )
        else:
            items.append((n, m.group(1), m.group(2)[:-3]))
    return items, findings


def _pages(root: Path) -> list[Path]:
    g = root / GUIDE
    return sorted(g.glob("*.md")) if g.is_dir() else []


def check_index(root: Path) -> list[str]:
    items, findings = _index(root)
    listed: dict[str, int] = {}
    for n, _title, slug in items:
        if slug in listed:
            findings.append(f"{GUIDE}/README.md:{n}: rule 1: `{slug}.md` is listed twice")
        listed.setdefault(slug, n)
        if not (root / GUIDE / f"{slug}.md").is_file():
            findings.append(f"{GUIDE}/README.md:{n}: rule 1: `{slug}.md` is listed but does not exist")
    for p in _pages(root):
        if p.name == "README.md":
            continue
        if p.stem not in listed:
            findings.append(f"{GUIDE}/{p.name}:1: rule 1: page is not listed in the index")
        if not re.fullmatch(r"[a-z0-9-]+", p.stem):
            findings.append(f"{GUIDE}/{p.name}:1: rule 1: slug must be [a-z0-9-]+")
    return findings


def check_links(root: Path) -> list[str]:
    findings = []
    for p in _pages(root):
        rel = _rel(root, p)
        for n, text in _prose(_lines(p)):
            for m in _LINK.finditer(text):
                if m.group(1):
                    continue  # images are rule 3
                target = m.group(2)
                if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
                    continue
                path, _, frag = target.partition("#")
                dest = (p.parent / path) if path else p
                if not dest.exists():
                    findings.append(f"{rel}:{n}: rule 2: link target `{path}` does not exist")
                elif frag and dest.suffix == ".md" and frag not in anchors(dest):
                    findings.append(f"{rel}:{n}: rule 2: no heading `#{frag}` in {path or p.name}")
    # console string literals
    listed = {s for _n, _t, s in _index(root)[0]} | {"faq"}
    web = root / WEB_SRC
    files = [f for ext in ("ts", "tsx") for f in web.rglob(f"*.{ext}")] if web.is_dir() else []
    for f in files:
        if "node_modules" in f.parts:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if line.lstrip().startswith(("*", "//", "/*")):
                continue  # a comment can use `/help/other` as an example
            for m in _HELP_LITERAL.finditer(line):
                slug, frag = m.group(1), m.group(2)
                rel = _rel(root, f)
                if slug not in listed:
                    findings.append(f"{rel}:{n}: rule 2: /help/{slug} is not in the guide index")
                elif frag:
                    page = root / GUIDE / f"{slug}.md"
                    if page.is_file() and frag not in anchors(page):
                        findings.append(f"{rel}:{n}: rule 2: no heading `#{frag}` in {slug}.md")
    return findings


def check_images(root: Path) -> list[str]:
    findings = []
    for p in _pages(root):
        rel = _rel(root, p)
        for n, text in _prose(_lines(p)):
            for m in _LINK.finditer(text):
                if m.group(1) and not re.match(r"[a-z]+:", m.group(2), re.I):
                    if not (p.parent / m.group(2).partition("#")[0]).is_file():
                        findings.append(f"{rel}:{n}: rule 3: image `{m.group(2)}` does not exist")
    img = root / IMAGES
    if img.is_dir():
        corpus = "\n".join(p.read_text(encoding="utf-8") for p in _md_files(root))
        for f in sorted(img.iterdir()):
            if not f.is_file():
                continue
            rel = _rel(root, f)
            if f.name not in corpus:
                findings.append(f"{rel}:1: rule 3: image is not referenced by any markdown file")
            if f.stat().st_size > MAX_IMAGE_BYTES:
                findings.append(f"{rel}:1: rule 3: image is over {MAX_IMAGE_BYTES // 1024} KB")
    return findings


def check_faq(root: Path) -> list[str]:
    if "faq" not in {s for _n, _t, s in _index(root)[0]}:
        return []
    p = root / GUIDE / "faq.md"
    if not p.is_file():
        return []  # rule 1 reports the missing file
    rel = _rel(root, p)
    findings: list[str] = []
    heads: list[tuple[int, int, str]] = []  # (line, level, text)
    body: dict[int, bool] = {}  # heading line -> has content before the next heading
    last = None
    fenced = False
    for n, line in enumerate(_lines(p), 1):
        if cd._FENCE.match(line):
            fenced = not fenced
            if last is not None:
                body[last] = True
            continue
        m = None if fenced else _HEADING.match(line)
        if m:
            heads.append((n, len(m.group(1)), m.group(2)))
            last = n
            body[n] = False
        elif line.strip() and last is not None:
            body[last] = True
    if not heads or heads[0][1] != 1:
        return [f"{rel}:1: rule 4: the page must start with a `#` title"]
    if sum(1 for h in heads if h[1] == 1) > 1:
        findings.append(f"{rel}:1: rule 4: more than one `#` title")
    seen: set[str] = set()
    group = None
    counts: dict[int, int] = {}
    for n, lvl, text in heads:
        if lvl >= 4:
            findings.append(f"{rel}:{n}: rule 4: headings deeper than `###` are not allowed")
        elif lvl == 2:
            group = n
            counts[n] = 0
        elif lvl == 3:
            if group is None:
                findings.append(f"{rel}:{n}: rule 4: question `{text}` is outside any `##` group")
            else:
                counts[group] += 1
            if not body[n]:
                findings.append(f"{rel}:{n}: rule 4: question `{text}` has no answer")
            if text.lower() in seen:
                findings.append(f"{rel}:{n}: rule 4: duplicate question `{text}`")
            seen.add(text.lower())
    for n, c in counts.items():
        if c == 0:
            findings.append(f"{rel}:{n}: rule 4: group has no `###` question")
    return findings


def _modules(root: Path) -> set[str]:
    """Same walk as check_docs._real_modules, over another root (for tests)."""
    names: set[str] = set()
    pkgs = root / "packages"
    bases = [pkgs / "core/cherrypick"]
    if pkgs.is_dir():
        bases += [p / "src/cherrypick" for p in pkgs.iterdir() if (p / "src/cherrypick").is_dir()]
    for base in bases:
        if base.is_dir():
            for f in base.rglob("*.py"):
                parts = list(f.relative_to(base.parent).with_suffix("").parts)
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if parts:
                    names.add(".".join(parts))
    return names


def check_commands(root: Path) -> list[str]:
    findings = []
    cli = root / cd._CLI_SOURCE
    cli_text = cli.read_text(encoding="utf-8") if cli.is_file() else ""
    block = cd._CLI_CHOICES.search(cli_text)
    verbs = set(cd._CLI_NAME.findall(block.group(1))) if block else set()
    modules = cd._real_modules() if root == ROOT else _modules(root)
    for p in _pages(root):
        rel = _rel(root, p)
        for n, seg in _code_segments(_lines(p)):
            for m in _RUN_PY.finditer(seg):
                verb = m.group(1)
                if verbs and verb not in verbs:
                    findings.append(f"{rel}:{n}: rule 5: `run.py {verb}` is not an orchestrator command")
                    continue
                for flag in _FLAG.findall(m.group(2)):
                    if f'"{flag}"' not in cli_text and f"'{flag}'" not in cli_text:
                        findings.append(f"{rel}:{n}: rule 7: `{flag}` is not in the orchestrator CLI")
            for m in _DASH_M.finditer(seg):
                if m.group(1).rstrip(".") not in modules:
                    findings.append(f"{rel}:{n}: rule 6: module `{m.group(1)}` does not exist")
    return findings


def check(root: Path = ROOT) -> list[str]:
    return check_index(root) + check_links(root) + check_images(root) + check_faq(root) + check_commands(root)


def main() -> int:
    findings = check()
    if not findings:
        print("check_guide: OK")
        return 0
    for f in sorted(findings):
        print(f)
    print(f"\ncheck_guide: {len(findings)} finding(s)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
