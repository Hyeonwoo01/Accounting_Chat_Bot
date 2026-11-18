"""DART OpenAPI collector (guide 2-3).

Saves raw responses under data/raw/dart/ before any cleaning:
    corpCode.zip
    list/<corp_code>_<bgn>_<end>_p<page>.json
    documents/<rcept_no>.zip
    failures.jsonl                 # failed document downloads, retried on the next run

Re-running is safe: list pages are re-fetched (new filings may appear) but documents
that already exist on disk are skipped, so only missing or previously failed ones are requested.
"""

from __future__ import annotations

import io
import json
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.collectors.http_client import CollectorError, RateLimitedClient
from src.common.config import RAW_DART_DIR, get_dart_api_key
from src.common.dart import viewer_url  # noqa: F401  (re-exported for callers)

logger = logging.getLogger(__name__)

API_BASE = "https://opendart.fss.or.kr/api"

STATUS_OK = "000"
STATUS_NO_DATA = "013"
# Statuses where continuing would only burn calls or fail the same way.
FATAL_STATUS = {
    "010": "unregistered key",
    "011": "unusable key",
    "012": "IP not allowed",
    "020": "request limit exceeded",
    "901": "key expired",
}

class DartFatalError(CollectorError):
    """DART returned a status that makes further requests pointless (bad key, daily limit)."""


@dataclass
class DocumentResult:
    rcept_no: str
    ok: bool
    path: Path | None = None
    error: str = ""
    skipped: bool = False


def _check_status(payload: dict[str, Any], context: str) -> None:
    status = str(payload.get("status", ""))
    if status in FATAL_STATUS:
        raise DartFatalError(f"{context}: DART status {status} ({FATAL_STATUS[status]})")
    if status not in (STATUS_OK, STATUS_NO_DATA):
        raise CollectorError(f"{context}: DART status {status} {payload.get('message', '')}")


def _parse_error_body(body: bytes) -> dict[str, Any]:
    """DART answers errors with JSON or XML instead of a zip; extract status/message."""
    text = body[:2000].decode("utf-8", "replace").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        root = ET.fromstring(text)
        return {"status": root.findtext("status", ""), "message": root.findtext("message", "")}
    except ET.ParseError:
        return {"status": "", "message": "unrecognized non-zip response"}


class DartCollector:
    def __init__(
        self,
        client: RateLimitedClient,
        raw_dir: Path = RAW_DART_DIR,
        api_key: str | None = None,
    ) -> None:
        self.client = client
        self.raw_dir = raw_dir
        self._api_key = api_key or get_dart_api_key()

    # ---------- corpCode.xml ----------

    def load_corp_codes(self, refresh: bool = False) -> list[dict[str, str]]:
        path = self.raw_dir / "corpCode.zip"
        if refresh or not path.exists():
            resp = self.client.get(f"{API_BASE}/corpCode.xml", params={"crtfc_key": self._api_key})
            if not resp.content.startswith(b"PK"):
                _check_status(_parse_error_body(resp.content), "corpCode.xml")
                raise CollectorError("corpCode.xml: response is not a zip file")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        return parse_corp_codes(path.read_bytes())

    # ---------- list.json ----------

    def fetch_filings(
        self, corp_code: str, start: date, end: date, pblntf_ty: str | None = None, page_count: int = 100
    ) -> list[dict[str, Any]]:
        """All filings of one company in [start, end], following pagination. Raw pages are saved."""
        bgn, end_de = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        filings: list[dict[str, Any]] = []
        page = 1
        while True:
            params = {
                "crtfc_key": self._api_key, "corp_code": corp_code, "bgn_de": bgn, "end_de": end_de,
                "page_no": str(page), "page_count": str(page_count),
            }
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            payload = self.client.get(f"{API_BASE}/list.json", params=params).json()
            _check_status(payload, f"list.json corp_code={corp_code} page={page}")

            # list.json does not echo pblntf_ty back, so the request (minus the key) is stored with it.
            suffix = f"_{pblntf_ty}" if pblntf_ty else ""
            out = self.raw_dir / "list" / f"{corp_code}_{bgn}_{end_de}{suffix}_p{page}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            request = {k: v for k, v in params.items() if k != "crtfc_key"}
            record = {"request": request, "response": payload}
            out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")

            if payload.get("status") == STATUS_NO_DATA:
                break
            filings.extend(payload.get("list", []))
            if page >= int(payload.get("total_page", 1)):
                break
            page += 1
        return filings

    def list_page(
        self, start: date, end: date, pblntf_ty: str | None = None, page: int = 1, page_count: int = 100
    ) -> dict[str, Any]:
        """One market-wide list.json page (no corp_code), not saved. For feasibility checks.

        Without corp_code DART limits the search window (about 3 months; confirm in the official docs),
        so callers must keep end - start short.
        """
        params = {
            "crtfc_key": self._api_key, "bgn_de": start.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"),
            "page_no": str(page), "page_count": str(page_count),
        }
        if pblntf_ty:
            params["pblntf_ty"] = pblntf_ty
        payload = self.client.get(f"{API_BASE}/list.json", params=params).json()
        _check_status(payload, f"list.json market {params['bgn_de']}-{params['end_de']} type={pblntf_ty} page={page}")
        return payload

    def collect_market_listings(self, start: date, end: date, pblntf_ty: str, page_count: int = 100) -> int:
        """Save every market-wide list.json page of one disclosure type, quarter by quarter.

        Raw pages: market/<type>_<bgn>_<end>_p<page>.json. Pages of windows that ended before
        today are cached and never re-requested, so an interrupted run resumes where it stopped.
        Returns the number of API calls made.
        """
        calls = 0
        for w_start, w_end in quarter_windows(start, end):
            cacheable = w_end < date.today()
            page, total_page = 1, 1
            while page <= total_page:
                bgn, end_de = w_start.strftime("%Y%m%d"), w_end.strftime("%Y%m%d")
                out = self.raw_dir / "market" / f"{pblntf_ty}_{bgn}_{end_de}_p{page}.json"
                if cacheable and out.exists():
                    payload = json.loads(out.read_text(encoding="utf-8"))["response"]
                else:
                    payload = self.list_page(w_start, w_end, pblntf_ty=pblntf_ty, page=page, page_count=page_count)
                    calls += 1
                    request = {"bgn_de": bgn, "end_de": end_de, "pblntf_ty": pblntf_ty,
                               "page_no": str(page), "page_count": str(page_count)}
                    out.parent.mkdir(parents=True, exist_ok=True)
                    tmp = out.with_suffix(".part")
                    tmp.write_text(json.dumps({"request": request, "response": payload}, ensure_ascii=False),
                                   encoding="utf-8")
                    tmp.replace(out)
                if payload.get("status") == STATUS_NO_DATA:
                    break
                total_page = int(payload.get("total_page", 1))
                logger.info("market %s %s~%s page %d/%d", pblntf_ty, w_start, w_end, page, total_page)
                page += 1
        return calls

    def fetch_filings_by_type(
        self, corp_code: str, start: date, end: date, types: list[str], page_count: int = 100
    ) -> list[dict[str, Any]]:
        """Query once per disclosure type so each filing carries its pblntf_ty (list.json omits it)."""
        filings: dict[str, dict[str, Any]] = {}
        for ty in types:
            for item in self.fetch_filings(corp_code, start, end, pblntf_ty=ty, page_count=page_count):
                filings.setdefault(item["rcept_no"], {**item, "pblntf_ty": ty})
        return list(filings.values())

    # ---------- document.xml (original filing zip) ----------

    def document_path(self, rcept_no: str) -> Path:
        return self.raw_dir / "documents" / f"{rcept_no}.zip"

    def download_document(self, rcept_no: str) -> DocumentResult:
        path = self.document_path(rcept_no)
        if path.exists():
            return DocumentResult(rcept_no, ok=True, path=path, skipped=True)
        try:
            resp = self.client.get(
                f"{API_BASE}/document.xml", params={"crtfc_key": self._api_key, "rcept_no": rcept_no}
            )
            if not resp.content.startswith(b"PK"):
                _check_status(_parse_error_body(resp.content), f"document.xml rcept_no={rcept_no}")
                raise CollectorError(f"document.xml rcept_no={rcept_no}: response is not a zip file")
            zipfile.ZipFile(io.BytesIO(resp.content)).testzip()
        except DartFatalError:
            raise
        except (CollectorError, zipfile.BadZipFile) as e:
            self._log_failure(rcept_no, str(e))
            return DocumentResult(rcept_no, ok=False, error=str(e))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(resp.content)
        tmp.replace(path)  # atomic: a crash never leaves a truncated zip that would be skipped later
        return DocumentResult(rcept_no, ok=True, path=path)

    def _log_failure(self, rcept_no: str, error: str) -> None:
        logger.warning("document download failed: %s", error)
        log = self.raw_dir / "failures.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            record = {"rcept_no": rcept_no, "error": error, "at": datetime.now().isoformat(timespec="seconds")}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def quarter_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Split [start, end] into calendar-quarter windows (market-wide list.json allows ~3 months)."""
    windows = []
    cur = start
    while cur <= end:
        q_end_month = ((cur.month - 1) // 3 + 1) * 3
        next_q = date(cur.year + (q_end_month == 12), q_end_month % 12 + 1, 1)
        w_end = min(end, date.fromordinal(next_q.toordinal() - 1))
        windows.append((cur, w_end))
        cur = next_q
    return windows


def parse_corp_codes(zip_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        root = ET.fromstring(zf.read(zf.namelist()[0]))
    return [
        {k: (el.findtext(k) or "").strip() for k in ("corp_code", "corp_name", "stock_code", "modify_date")}
        for el in root.iter("list")
    ]


def find_corps(corps: list[dict[str, str]], names: list[str]) -> list[dict[str, str]]:
    """Candidates whose corp_name contains any of `names`. A human must confirm the right one."""
    return [c for c in corps if any(n in c["corp_name"] for n in names)]
