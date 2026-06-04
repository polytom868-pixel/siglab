"""SigLab TUI — Terminal User Interface built with Textual."""

__all__ = ["SigLabTUI"]


def __getattr__(name: str):
    if name == "SigLabTUI":
        from siglab.tui.app import SigLabTUI

        return SigLabTUI
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
