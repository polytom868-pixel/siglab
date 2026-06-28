from fastapi import FastAPI
import sys
from pathlib import Path

_api_dir = Path(__file__).resolve().parent
for p in [_api_dir, _api_dir.parent, Path("/var/task"), Path("/var/task/api")]:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

app = FastAPI()
import_error = None

try:
    from siglab.dashboard.routes import app as _real_app
    app = _real_app
except Exception as e:
    import_error = f"{type(e).__name__}: {e}"
    # Also try to import traceback
    import traceback
    import_error += "\n" + traceback.format_exc()

@app.get("/health")
async def health():
    if import_error:
        return {"status": "error", "detail": import_error, "paths": sys.path[:5]}
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "error" if import_error else "ok", "detail": import_error}

handler = app
