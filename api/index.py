import sys
from pathlib import Path

# Ensure the project root is on sys.path so siglab/ is importable
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from siglab.dashboard.routes import app

handler = app
