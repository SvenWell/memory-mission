"""Memory Mission — governed context engine for AI agents.

Files-first substrate with operating + evidence memory layers,
individual + firm planes, review-gated promotion.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("memory-mission")
except PackageNotFoundError:  # pragma: no cover - editable install without metadata
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
