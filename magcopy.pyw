#!/usr/bin/env python3
"""Double-click launcher: runs MagCopy with no console window."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from magcopy.main import main

if __name__ == "__main__":
    sys.exit(main())
