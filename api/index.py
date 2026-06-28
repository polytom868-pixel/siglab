import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from siglab.dashboard.routes import app

handler = app
