#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from appendix.server import serve

if __name__ == "__main__":
    serve("viewer", "127.0.0.1", 0, True)
