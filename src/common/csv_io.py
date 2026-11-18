"""Read CSV files that people edited in Excel (Korean Windows).

Excel re-saves a UTF-8 CSV as cp949 ("CSV (쉼표로 분리)") or as UTF-16 tab-separated
("유니코드 텍스트"), depending on the option picked. Review sheets go through Excel, so
readers must accept all three instead of failing (or mis-reading) on the second save.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pandas as pd


def decode_excel_text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949")


def read_excel_csv(path: Path, **kwargs) -> pd.DataFrame:
    """pandas.read_csv for a file Excel may have re-saved (UTF-8, cp949 or UTF-16 tab)."""
    text = decode_excel_text(Path(path).read_bytes())
    header = text.splitlines()[0] if text else ""
    sep = "\t" if header.count("\t") > header.count(",") else ","
    return pd.read_csv(io.StringIO(text), sep=sep, quoting=csv.QUOTE_MINIMAL, **kwargs)
