"""Verify the Harness Engineering document map and local Markdown links."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_PATHS = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "docs/DESIGN.md",
    "docs/FRONTEND.md",
    "docs/PLANS.md",
    "docs/PRODUCT_SENSE.md",
    "docs/QUALITY_SCORE.md",
    "docs/RELIABILITY.md",
    "docs/SECURITY.md",
    "docs/design-docs/index.md",
    "docs/exec-plans/active/001-aws-connect.md",
    "docs/exec-plans/tech-debt-tracker.md",
    "docs/product-specs/index.md",
)
LINK_PATTERN = re.compile(r"(?<!!)\[[^]]*]\(([^)]+)\)")
FENCED_CODE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_PATTERN = re.compile(r"`[^`\n]*`")


def local_markdown_links(document: Path) -> list[Path]:
    """Return repository-local file targets referenced by a Markdown document."""

    targets: list[Path] = []
    content = document.read_text(encoding="utf-8")
    prose = FENCED_CODE_PATTERN.sub("", content)
    prose = INLINE_CODE_PATTERN.sub("", prose)
    for raw_target in LINK_PATTERN.findall(prose):
        target = raw_target.strip().split("#", maxsplit=1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "#")):
            continue
        targets.append((document.parent / target).resolve())
    return targets


def verify(root: Path) -> list[str]:
    """Return human-readable violations for missing map entries or broken links."""

    errors = [
        f"missing required path: {path}" for path in REQUIRED_PATHS if not (root / path).exists()
    ]
    for document in sorted(root.rglob("*.md")):
        if any(part in {".git", ".venv"} for part in document.parts):
            continue
        for target in local_markdown_links(document):
            if not target.exists():
                errors.append(f"broken link: {document.relative_to(root)} -> {target}")
    return errors


def main() -> int:
    """Verify documentation from the repository root."""

    root = Path(__file__).resolve().parents[1]
    errors = verify(root)
    if errors:
        print("Documentation verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Documentation verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
