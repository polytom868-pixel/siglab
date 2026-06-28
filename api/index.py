from fastapi import FastAPI

# Placeholder for Vercel static analysis — replaced below
app = FastAPI()

import sys
from pathlib import Path
_api_dir = Path(__file__).resolve().parent
for p in [_api_dir, _api_dir.parent]:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from siglab.dashboard.routes import app as _siglab_app
app = _siglab_app
handler = app
