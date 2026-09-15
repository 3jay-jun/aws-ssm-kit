"""Enforce branch coverage for security- and contract-critical modules."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

CRITICAL_BRANCH_FILES = (
    "src/aws_connect/application/authentication_service.py",
    "src/aws_connect/application/authenticated_operation.py",
    "src/aws_connect/infrastructure/masking.py",
    "src/aws_connect/presentation/cli/errors.py",
    "src/aws_connect/presentation/gui/errors.py",
)


def verify_critical_branch_coverage(report: Path) -> list[str]:
    """Return violations when a required file is absent or below 100% branch coverage."""

    try:
        root = ElementTree.parse(report).getroot()
    except (OSError, ElementTree.ParseError) as error:
        return [f"coverage report unreadable: {type(error).__name__}"]

    rates = {
        PurePosixPath(item.attrib["filename"].replace("\\", "/")).as_posix(): item.attrib.get(
            "branch-rate"
        )
        for item in root.findall(".//class")
        if "filename" in item.attrib
    }
    errors: list[str] = []
    for filename in CRITICAL_BRANCH_FILES:
        rate = rates.get(filename)
        if rate is None:
            errors.append(f"critical coverage target missing: {filename}")
        elif rate != "1":
            errors.append(f"critical branch coverage below 100%: {filename} ({rate})")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Validate the pytest-cov XML report produced by the current check run."""

    arguments = argv if argv is not None else sys.argv[1:]
    report = Path(arguments[0]) if arguments else Path("coverage.xml")
    errors = verify_critical_branch_coverage(report)
    if errors:
        print("Critical branch coverage verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Critical branch coverage verification passed (5 files at 100%).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
