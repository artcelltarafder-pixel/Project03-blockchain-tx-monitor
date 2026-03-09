"""
Entry point — run from project root:
    python3 demo/run_demo.py
    python3 demo/run_demo.py --mode B --speed 5
"""
import sys
import os

# Ensure project root is on path so `demo` package resolves
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from demo.demo_runner import main

if __name__ == "__main__":
    asyncio.run(main())
