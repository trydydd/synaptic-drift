"""Error hierarchy for the eval harness (tests/evals/)."""

from __future__ import annotations

from synd.errors import SyndError


class EvalError(SyndError):
    """Base class for eval harness failures."""


class EvalDatasetError(EvalError):
    """Malformed dataset or unresolvable gold ref."""
