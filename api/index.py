import sys
from pathlib import Path

# Ensure the project source is importable in Vercel's Lambda environment
_api_dir = Path(__file__).resolve().parent
_root = _api_dir.parent

for p in [_api_dir, _root, Path("/var/task"), Path("/var/task/api")]:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from siglab.dashboard.routes import app

handler = app
