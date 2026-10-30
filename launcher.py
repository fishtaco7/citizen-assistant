"""PyInstaller entry point.

Kept at the repository root so ``app`` stays a package and its relative
imports keep working inside the frozen executable.
"""
from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
