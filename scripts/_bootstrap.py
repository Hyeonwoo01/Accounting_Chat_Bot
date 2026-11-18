"""Make `src` importable when scripts are run as `python scripts/<name>.py` from anywhere."""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _excepthook(exc_type, exc, tb):
    # Missing keys are a setup problem, not a bug: show the one-line instruction, not a traceback.
    from src.common.config import MissingSecretError

    if issubclass(exc_type, MissingSecretError):
        print(f"[setup] {exc}", file=sys.stderr)
        sys.exit(1)
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _excepthook
