"""Secret-safe diagnostic log archive export."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

from aws_connect.application.ports import PrivateFileAccess
from aws_connect.domain.errors import ConfigurationError
from aws_connect.infrastructure.managed_logs import managed_log_files
from aws_connect.infrastructure.masking import mask_text


class MaskedDiagnosticLogExporter:
    """Re-mask every exported log instead of trusting its original handler."""

    def __init__(self, file_access: PrivateFileAccess | None = None) -> None:
        self._file_access = file_access

    def export(self, source_directory: Path, destination: Path) -> int:
        source = source_directory.expanduser().resolve()
        target = destination.expanduser().resolve()
        if target.suffix.lower() != ".zip":
            raise ConfigurationError(
                message_code="diagnostics.destination.zip_required",
                technical_cause="Diagnostic destination must use the .zip extension",
            )
        if source == target.parent or source in target.parents:
            raise ConfigurationError(
                message_code="diagnostics.destination.inside_logs",
                technical_cause="Diagnostic archive cannot be written inside the log directory",
            )
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                if self._file_access is not None:
                    self._file_access.restrict(temporary)
                if source.is_dir():
                    for path in managed_log_files(source):
                        text = path.read_text(encoding="utf-8", errors="replace")
                        archive.writestr(path.name, mask_text(text))
                        count += 1
                archive.writestr(
                    "README.txt",
                    "AWS Connect diagnostic logs. "
                    "Sensitive patterns were re-masked during export.\n",
                )
            os.replace(temporary, target)
            if self._file_access is not None:
                self._file_access.restrict(target)
            return count
        except OSError as error:
            raise ConfigurationError(
                message_code="diagnostics.export.failed",
                technical_cause=type(error).__name__,
            ) from error
        finally:
            temporary.unlink(missing_ok=True)
