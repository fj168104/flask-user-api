"""Custom exceptions used by the TextIn provider.

Layered:
  TextInError
    TextInAPIError    — TextIn business error (HTTP 200 but code != 200)
  LookupError
    ExtractionNotFound     — record JSON missing
    UploadedFileNotFound   — original upload missing
  RuntimeError
    RecordParseError       — stored JSON is corrupt
    OcrHTTPError           — logical validation error carrying HTTP status,
                              translated to {code, msg, data} by the blueprint
"""

from __future__ import annotations

from typing import Any


class TextInError(RuntimeError):
    """Base class for TextIn integration errors."""


class TextInAPIError(TextInError):
    """TextIn business error (HTTP 200 but code != 200)."""

    def __init__(self, code: int | str, message: str) -> None:
        super().__init__(f"TextIn API error {code}: {message}")
        self.code = code
        self.message = message


class ExtractionNotFound(LookupError):
    """The requested extraction_id is not present in local storage."""


class UploadedFileNotFound(LookupError):
    """The original upload file for an extraction is missing."""


class RecordParseError(RuntimeError):
    """A persisted extraction JSON could not be parsed."""


class OcrHTTPError(Exception):
    """Logical OCR error meant to be rendered as {code, msg, data}.

    Routes raise this for validation failures; a single blueprint
    errorhandler converts it to a Flask JSON response.
    """

    def __init__(
        self,
        status: int,
        code: str,
        msg: str,
        data: Any = None,
    ) -> None:
        super().__init__(msg)
        self.status = status
        self.code = code
        self.msg = msg
        self.data = data