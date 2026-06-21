"""Allow ``python -m siglab.tui`` to launch the TUI."""
from siglab.tui.app import SigLabTUI

def main() -> None:
    SigLabTUI().run()
if __name__ == '__main__':
    main()