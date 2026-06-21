"""SigLab TUI — Terminal User Interface built with Textual."""
from typing import Any
__all__ = ['SigLabTUI']

def __getattr__(name: str) -> Any:
    if name == 'SigLabTUI':
        from siglab.tui.app import SigLabTUI
        return SigLabTUI
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')