"""Publication gate binding manual VM evidence to the exact candidate bytes."""

import argparse
import hashlib
import json
from pathlib import Path

CASES = frozenset(
    {
        "offline_install",
        "zip_core_without_winfsp",
        "uac_cancel",
        "alternate_admin",
        "reboot",
        "upgrade",
        "active_connection_guard",
        "uninstall_data_preserved",
        "s3_read_connect_disconnect",
    }
)


def verify(directory: Path, evidence: dict, *, run_id: str) -> str:
    candidate = json.loads((directory / "release-candidate.json").read_text(encoding="utf-8-sig"))
    ref = candidate["ref"]
    if not ref.startswith("refs/tags/v") or str(candidate["run_id"]) != run_id:
        raise ValueError("Candidate must be from the requested release tag run")
    expected = {item["name"]: item["sha256"] for item in candidate["files"]}
    if (
        len(expected) != 2
        or not any(name.endswith(".zip") for name in expected)
        or not any(name.endswith("Setup.exe") for name in expected)
    ):
        raise ValueError("Both production artifact types are required")
    if evidence.get("files") != expected or evidence.get("commit") != candidate["commit"]:
        raise ValueError("Evidence is for different candidate bytes or commit")
    if not evidence.get("reviewer"):
        raise ValueError("VM evidence reviewer required")
    for name, checksum in expected.items():
        if Path(name).name != name or "TEST-ONLY" in name:
            raise ValueError("Unsafe or TEST-ONLY artifact name")
        with (directory / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("Candidate file changed since verification")
    systems = evidence.get("systems", {})
    for os_name in ("windows10", "windows11"):
        result = systems.get(os_name, {})
        if not result.get("os_build") or not result.get("initial_winfsp"):
            raise ValueError("VM baseline missing")
        cases = result.get("cases", {})
        if any(cases.get(case) != "passed" for case in CASES):
            raise ValueError(f"VM cases incomplete: {os_name}")
    return ref.removeprefix("refs/tags/")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        tag = verify(
            args.directory,
            json.loads(args.evidence.read_text(encoding="utf-8-sig")),
            run_id=args.run_id,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Release evidence rejected: {error}\n")
    print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
