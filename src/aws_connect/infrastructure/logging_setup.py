"""Rotating, centrally masked application logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aws_connect.application.ports import PrivateFileAccess
from aws_connect.domain.app_settings import AppSettings
from aws_connect.domain.errors import ApplicationError, ConfigurationError
from aws_connect.infrastructure.managed_logs import managed_log_files
from aws_connect.infrastructure.masking import MaskingFilter

_HANDLER_MARKER = "aws-connect-managed"


class RotatingLogConfigurator:
    """Own exactly one rotating handler for the ``aws_connect`` logger tree."""

    def __init__(
        self,
        file_access: PrivateFileAccess | None = None,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        backups: int = 5,
    ) -> None:
        self._file_access = file_access
        self._max_bytes = max_bytes
        self._backups = backups

    def apply(self, settings: AppSettings) -> Path:
        path = settings.log_directory.expanduser().resolve() / "aws-connect.log"
        handler: RotatingFileHandler | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if self._file_access is not None:
                self._file_access.restrict(path.parent)
                for existing_log in managed_log_files(path.parent):
                    self._file_access.restrict(existing_log)
            handler = RotatingFileHandler(
                path,
                maxBytes=self._max_bytes,
                backupCount=self._backups,
                encoding="utf-8",
            )
            if self._file_access is not None:
                self._file_access.restrict(path)
        except ApplicationError:
            if handler is not None:
                handler.close()
            raise
        except OSError as error:
            if handler is not None:
                handler.close()
            raise ConfigurationError(
                message_code="settings.log_directory.unwritable",
                technical_cause=type(error).__name__,
            ) from error
        handler.name = _HANDLER_MARKER
        handler.addFilter(MaskingFilter())
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        logger = logging.getLogger("aws_connect")
        previous = [item for item in logger.handlers if item.name == _HANDLER_MARKER]
        for item in previous:
            logger.removeHandler(item)
            item.close()
        logger.addHandler(handler)
        logger.setLevel(settings.log_level.value)
        # Activity history remains complete regardless of diagnostic verbosity.
        logging.getLogger("aws_connect.activity").setLevel(logging.INFO)
        logger.propagate = False
        return path
