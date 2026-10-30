"""The viewers, one per kind. Each loads a release's files and answers its JSON paths."""

from __future__ import annotations


class NotFound(Exception):
    """No such thing in this release."""


class BadRequest(Exception):
    """A parameter out of range or malformed."""
