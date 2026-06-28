from fastapi import FastAPI
import sys
from pathlib import Path
import traceback

_api_dir = Path(__file__).resolve().parent
for p in [_api_dir, _api_dir.parent]:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

app = FastAPI()
import_error = None

try:
    from siglab.dashboard.routes import app as _real_app
    app = _real_app
except Exception:
    import_error = traceback.format_exc()

@app.get("/health")
async def health():
    if import_error:
        return {"status": "error", "detail": import_error, "paths": [str(p) for p in sys.path[:5]]}
    return {"status": "ok"}

handler = app
