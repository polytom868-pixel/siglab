import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Just test if basic Python works
from fastapi import FastAPI

app = FastAPI(title="SigLab Dashboard")

@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/")
async def root():
    return {"message": "SigLab Dashboard API"}

handler = app
