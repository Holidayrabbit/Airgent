"""Allow `python -m app ...` — used by the pi memory extension to reach the
same SQLite store from any working directory."""

from app.cli import main

if __name__ == "__main__":
    main()
