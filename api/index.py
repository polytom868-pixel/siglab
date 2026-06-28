import sys
from pathlib import Path

_api_dir = Path(__file__).resolve().parent
for p in [_api_dir, _api_dir.parent]:
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

try:
    from siglab.dashboard.routes import app
except Exception as exc:
    from fastapi import FastAPI
    app = FastAPI(title="SigLab")
    
    error_detail = str(exc)
    
    @app.get("/")
    async def root():
        return {"error": error_detail}
    
    @app.get("/health")
    async def health():
        return {"status": "error", "detail": error_detail}

handler = app
