import hashlib
import json

import pytest
from tools.verify_release_evidence import CASES, verify


def candidate(tmp_path):
    names = ["app.zip", "app-Setup.exe"]
    for name in names:
        (tmp_path / name).write_bytes(b"payload")
    files = {name: hashlib.sha256(b"payload").hexdigest() for name in names}
    (tmp_path / "release-candidate.json").write_text(
        json.dumps(
            {
                "ref": "refs/tags/v1.0.1",
                "run_id": "42",
                "commit": "commit",
                "files": [{"name": n, "sha256": h} for n, h in files.items()],
            }
        )
    )
    evidence = {
        "files": files,
        "commit": "commit",
        "reviewer": "fixture",
        "systems": {
            os: {
                "os_build": "fixture",
                "initial_winfsp": "missing",
                "cases": dict.fromkeys(CASES, "passed"),
            }
            for os in ("windows10", "windows11")
        },
    }
    return evidence


def test_exact_tested_bytes_are_required(tmp_path):
    evidence = candidate(tmp_path)
    assert verify(tmp_path, evidence, run_id="42") == "v1.0.1"
    (tmp_path / "app.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        verify(tmp_path, evidence, run_id="42")


def test_missing_vm_cases_or_wrong_run_block_publication(tmp_path):
    evidence = candidate(tmp_path)
    with pytest.raises(ValueError, match="requested"):
        verify(tmp_path, evidence, run_id="43")
    evidence["systems"]["windows10"]["cases"].pop("uac_cancel")
    with pytest.raises(ValueError, match="incomplete"):
        verify(tmp_path, evidence, run_id="42")


def test_production_docs_ignore_external_caches_but_check_project_links(tmp_path):
    from tools.verify_docs import verify as verify_docs

    (tmp_path / "graft").mkdir()
    (tmp_path / "graft/cache.md").write_text("[broken](absent)")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/project.md").write_text("[broken](absent)")
    failures = verify_docs(tmp_path)
    assert any("project.md" in error for error in failures)
    assert not any("cache.md" in error for error in failures)
