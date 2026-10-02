import sys
from pathlib import Path

# File logging so the server also runs windowless (pythonw.exe),
# where stdout/stderr don't exist.
_LOG = Path(__file__).resolve().parent / "server.log"
sys.stdout = open(_LOG, "a", buffering=1)
sys.stderr = sys.stdout

from app import app

if __name__ == "__main__":
    print("Starting MyShop on http://127.0.0.1:5000", flush=True)
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
