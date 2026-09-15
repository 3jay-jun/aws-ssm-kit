"""Generate a deterministic third-party dependency notice for the Portable ZIP."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path

RUNTIME_DISTRIBUTIONS = (
    "boto3",
    "botocore",
    "jmespath",
    "PySide6",
    "PySide6_Addons",
    "PySide6_Essentials",
    "s3transfer",
    "shiboken6",
    "urllib3",
)


def distribution_notice(name: str) -> str:
    """Return stable license metadata for one required runtime distribution."""

    distribution = metadata.distribution(name)
    fields = distribution.metadata
    license_expression = fields.get("License-Expression") or fields.get("License") or "UNKNOWN"
    project_url = fields.get("Home-page") or next(
        (
            value.split(",", 1)[1].strip()
            for value in fields.get_all("Project-URL", [])
            if "," in value
        ),
        "UNKNOWN",
    )
    return "\n".join(
        (
            f"Package: {distribution.metadata['Name']}",
            f"Version: {distribution.version}",
            f"License: {license_expression}",
            f"Project: {project_url}",
        )
    )


def render_notice() -> str:
    """Render all runtime dependency notices in an intentional fixed order."""

    sections = [
        "AWS Connect third-party dependency inventory",
        "Generated from installed package metadata; retain with the Portable ZIP.",
    ]
    sections.extend(distribution_notice(name) for name in RUNTIME_DISTRIBUTIONS)
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_notice(), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
