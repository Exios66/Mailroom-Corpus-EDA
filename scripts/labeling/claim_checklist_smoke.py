"""Offline checklist audit; invalid accepted values exit nonzero, not a gold gate.

Compatibility entry point for the shared labeling scorer. Unlike the old regex
matrix, general coverage language does not imply a claim determination and held
rows are not counted as accepted labels. Use --stage to choose a source snapshot.
"""
from mailroom_eda.labeling.scoring import main

if __name__ == "__main__":
    raise SystemExit(main())
