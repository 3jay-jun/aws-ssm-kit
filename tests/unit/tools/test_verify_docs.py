from pathlib import Path

from tools.verify_docs import local_markdown_links


def test_code_that_resembles_markdown_link_is_ignored(tmp_path: Path) -> None:
    document = tmp_path / "sample.md"
    document.write_text(
        "`OperationResult[T](operation_id)`\n\n```text\nValue[T](value)\n```\n",
        encoding="utf-8",
    )

    assert local_markdown_links(document) == []
