from pathlib import Path

from tools.verify_critical_coverage import (
    CRITICAL_BRANCH_FILES,
    verify_critical_branch_coverage,
)


def _write_report(path: Path, entries: list[tuple[str, str]]) -> None:
    classes = "".join(
        f'<class filename="{filename}" branch-rate="{rate}" />' for filename, rate in entries
    )
    path.write_text(f"<coverage><packages><class>{classes}</class></packages></coverage>")


def test_accepts_every_critical_file_at_full_branch_coverage(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    _write_report(report, [(filename, "1") for filename in CRITICAL_BRANCH_FILES])

    assert verify_critical_branch_coverage(report) == []


def test_reports_missing_and_below_target_files(tmp_path: Path) -> None:
    report = tmp_path / "coverage.xml"
    _write_report(
        report,
        [
            (CRITICAL_BRANCH_FILES[0].replace("/", "\\"), "0.99"),
            *((filename, "1") for filename in CRITICAL_BRANCH_FILES[1:-1]),
        ],
    )

    errors = verify_critical_branch_coverage(report)

    assert errors == [
        f"critical branch coverage below 100%: {CRITICAL_BRANCH_FILES[0]} (0.99)",
        f"critical coverage target missing: {CRITICAL_BRANCH_FILES[-1]}",
    ]


def test_reports_unreadable_or_malformed_xml(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xml"
    malformed = tmp_path / "coverage.xml"
    malformed.write_text("<coverage>", encoding="utf-8")

    assert verify_critical_branch_coverage(missing) == [
        "coverage report unreadable: FileNotFoundError"
    ]
    assert verify_critical_branch_coverage(malformed) == ["coverage report unreadable: ParseError"]
