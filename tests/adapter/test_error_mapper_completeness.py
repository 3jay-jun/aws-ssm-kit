from aws_connect.domain.errors import ApplicationError
from aws_connect.presentation.cli.errors import mapped_error_types
from aws_connect.presentation.gui.errors import mapped_error_types_for_gui


def test_every_typed_error_has_cli_and_gui_mapping() -> None:
    defined = frozenset(ApplicationError.__subclasses__())

    assert mapped_error_types() == defined
    assert mapped_error_types_for_gui() == defined
