"""SigLab TUI — Terminal User Interface built with Textual."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from siglab.tui.app import SigLabTUI
else:

    def __getattr__(name: str):
        if name == "SigLabTUI":
            from siglab.tui.app import SigLabTUI

            return SigLabTUI
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["SigLabTUI"]
