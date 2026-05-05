import logging

from app import logging_config


def test_configure_logging_skips_console_handler_by_default_on_windows(tmp_path, monkeypatch) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    for handler in original_handlers:
        root_logger.removeHandler(handler)
    monkeypatch.setattr(logging_config.os, "name", "nt")
    monkeypatch.delenv("CODE_REVIEW_CONSOLE_LOG", raising=False)
    try:
        logging_config.configure_logging(tmp_path)

        stream_handlers = [
            handler
            for handler in root_logger.handlers
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        ]

        assert stream_handlers == []
        assert any(isinstance(handler, logging.FileHandler) for handler in root_logger.handlers)
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root_logger.addHandler(handler)


def test_configure_logging_allows_console_handler_when_explicitly_enabled(tmp_path, monkeypatch) -> None:
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    for handler in original_handlers:
        root_logger.removeHandler(handler)
    monkeypatch.setattr(logging_config.os, "name", "nt")
    monkeypatch.setenv("CODE_REVIEW_CONSOLE_LOG", "true")
    try:
        logging_config.configure_logging(tmp_path)

        assert any(
            isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
            for handler in root_logger.handlers
        )
    finally:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)
            handler.close()
        for handler in original_handlers:
            root_logger.addHandler(handler)
