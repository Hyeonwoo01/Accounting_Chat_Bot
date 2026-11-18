"""Turn raw news/DART files into `documents` rows (guide 5-1).

Reads only data/raw/ and config; never calls external APIs.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import zipfile
from collections.abc import Iterator
from datetime import date, datetime
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.common.config import PROJECT_ROOT, Period
from src.common.dart import viewer_url
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.text_cleaning import clean_text, clean_title, content_hash, normalize_for_compare

logger = logging.getLogger(__name__)

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------- news

def news_doc_id(url: str) -> str:
    # Deterministic (unlike a running number) so re-runs map the same article to the same ID.
    return "NEWS_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def source_domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.").removeprefix("m.")


def iter_raw_news(raw_news_dir: Path) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any]]]:
    """Yield (raw_path, query_info, item) from saved Naver responses, in a stable order."""
    for path in sorted(raw_news_dir.glob("*/*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for item in record["response"].get("items", []):
            yield path, record["query"], item


def build_news_docs(raw_news_dir: Path, period: Period, matcher: CompanyMatcher) -> list[dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for path, query, item in iter_raw_news(raw_news_dir):
        url = item.get("originallink") or item.get("link")
        if not url:
            continue
        doc_id = news_doc_id(url)
        if doc_id in docs:  # same article found by several queries: first query wins
            continue
        published = parsedate_to_datetime(item["pubDate"]).date()
        if not (period.start <= published <= period.end):
            continue
        title = clean_title(item.get("title"))
        content = clean_text(item.get("description"))
        docs[doc_id] = {
            "doc_id": doc_id,
            "source_type": "news",
            "title": title,
            "content": content,
            "published_at": published.isoformat(),
            "url": url,
            "source_name": source_domain(item.get("originallink") or ""),  # API has no outlet name
            "company_ids": matcher.match(f"{title} {content}"),
            "search_keyword": query["keyword"],
            "content_hash": content_hash(title, content),
            "is_relevant": True,  # set to False after EDA relevance review
            "raw_path": _rel(path),
            "collected_at": _now(),
        }
    return list(docs.values())


def assign_dup_groups(docs: list[dict[str, Any]], threshold: float = 0.85, window_days: int = 3) -> dict[str, str]:
    """Group near-duplicate titles published within `window_days` (kept, not deleted: volume matters).

    Returns {doc_id: group_id}; group_id is the earliest doc_id of the group.
    """
    ordered = sorted(docs, key=lambda d: (d["published_at"], d["doc_id"]))
    groups: dict[str, str] = {}
    recent: list[tuple[date, str, str]] = []  # (date, normalized title, group_id)
    for d in ordered:
        day = date.fromisoformat(d["published_at"])
        norm = normalize_for_compare(d["title"])
        recent = [r for r in recent if (day - r[0]).days <= window_days]
        group = d["doc_id"]
        for _, other, other_group in recent:
            if norm and SequenceMatcher(None, norm, other).ratio() >= threshold:
                group = other_group
                break
        groups[d["doc_id"]] = group
        recent.append((day, norm, group))
    return groups


# ---------------------------------------------------------------- DART

_TITLE_TAG = re.compile(r"<TITLE[^>]*>(.*?)</TITLE>", re.IGNORECASE | re.DOTALL)
_CELL_END = re.compile(r"</(?:TD|TH|TE|TU)>", re.IGNORECASE)
_BLOCK_END = re.compile(r"</(?:P|TR|TABLE|SECTION-\d|LIBRARY)>|<BR\s*/?>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t ]+")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def extract_dart_text(xml_text: str) -> str:
    """Strip DART XML/HTML markup, keeping section titles as '## <title>' lines for chunking.

    Regex instead of an XML parser: DART documents are frequently not well-formed.
    """
    text = _TITLE_TAG.sub(lambda m: f"\n## {_ANY_TAG.sub('', m.group(1)).strip()}\n", xml_text)
    text = _CELL_END.sub(" ", text)
    text = _BLOCK_END.sub("\n", text)
    text = html.unescape(_ANY_TAG.sub("", text))
    lines = (_SPACES.sub(" ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def extract_dart_zip(zip_path: Path, rcept_no: str) -> str:
    """Main document first (<rcept_no>.xml), then attachments such as audit reports."""
    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(n for n in zf.namelist() if n.lower().endswith((".xml", ".html", ".htm")))
        names.sort(key=lambda n: Path(n).stem != rcept_no)
        return "\n\n".join(extract_dart_text(_decode(zf.read(n))) for n in names)


def iter_raw_filings(raw_dart_dir: Path) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any]]]:
    for path in sorted((raw_dart_dir / "list").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for item in record["response"].get("list", []):
            yield path, record["request"], item


def corp_to_company(mapping: dict[str, Any]) -> dict[str, str]:
    return {
        str(e["corp_code"]): c["company_id"]
        for c in mapping["companies"]
        for e in c.get("kr_entities", [])
        if e.get("corp_code")
    }


def build_dart_docs(raw_dart_dir: Path, mapping: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Returns (docs, stats). Filings whose original was not downloaded get empty content."""
    corp_map = corp_to_company(mapping)
    docs: dict[str, dict[str, Any]] = {}
    stats = {"filings": 0, "with_text": 0, "extract_failed": 0, "no_document": 0}
    for path, request, item in iter_raw_filings(raw_dart_dir):
        rcept_no = item["rcept_no"]
        doc_id = f"DART_{rcept_no}"
        if doc_id in docs:
            continue
        stats["filings"] += 1
        zip_path = raw_dart_dir / "documents" / f"{rcept_no}.zip"
        content = ""
        if zip_path.exists():
            try:
                content = extract_dart_zip(zip_path, rcept_no)
                stats["with_text"] += bool(content)
            except (zipfile.BadZipFile, OSError) as e:
                logger.warning("text extraction failed for %s: %s", rcept_no, e)
                stats["extract_failed"] += 1
        else:
            stats["no_document"] += 1
        rcept_dt = datetime.strptime(item["rcept_dt"], "%Y%m%d").date().isoformat()
        report_nm = re.sub(r"\s+", " ", item["report_nm"]).strip()
        corp_code = item["corp_code"]
        docs[doc_id] = {
            "doc_id": doc_id,
            "source_type": "dart",
            "title": report_nm,
            "content": content,
            "published_at": rcept_dt,
            "url": viewer_url(rcept_no),
            "source_name": item.get("flr_nm"),
            "company_ids": [corp_map[corp_code]] if corp_code in corp_map else [],
            "corp_code": corp_code,
            "rcept_no": rcept_no,
            "report_nm": report_nm,
            "rcept_dt": rcept_dt,
            "pblntf_ty": request.get("pblntf_ty"),
            "content_hash": content_hash(report_nm, content),
            "is_relevant": True,
            "raw_path": _rel(zip_path if zip_path.exists() else path),
            "collected_at": _now(),
        }
    return list(docs.values()), stats


# ---------------------------------------------------------------- Big4-filed audit reports (branch B-1)

def filer_to_company(mapping: dict[str, Any]) -> dict[str, str]:
    """Korean entity name -> company_id, for matching list.json `flr_nm`."""
    return {e["name"]: c["company_id"] for c in mapping["companies"] for e in c.get("kr_entities", [])}


def _filer_company(flr_nm: str, filers: dict[str, str]) -> str | None:
    return next((cid for name, cid in filers.items() if name in (flr_nm or "")), None)


def iter_market_listings(raw_dart_dir: Path) -> Iterator[tuple[Path, dict[str, Any], dict[str, Any]]]:
    for path in sorted((raw_dart_dir / "market").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for item in record["response"].get("list", []):
            yield path, record["request"], item


def build_audit_report_docs(
    raw_dart_dir: Path, mapping: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rows for filings submitted BY a Big4 firm (mostly client audit reports), plus monthly counts.

    company_ids holds the filing firm; corp_code is the audited client. These are the client's
    financial statements, never facts about the Big4 firm itself (CLAUDE.md 원칙 5·6).
    Monthly counts cover every filer ("other" = non-Big4) so EDA can compute Big4 share by count.
    """
    filers = filer_to_company(mapping)
    docs: dict[str, dict[str, Any]] = {}
    monthly: dict[tuple[str, str, str], int] = {}
    seen: set[str] = set()
    for path, request, item in iter_market_listings(raw_dart_dir):
        rcept_no = item["rcept_no"]
        if rcept_no in seen:
            continue
        seen.add(rcept_no)
        company_id = _filer_company(item.get("flr_nm", ""), filers)
        month = f"{item['rcept_dt'][:4]}-{item['rcept_dt'][4:6]}"
        key = (month, request.get("pblntf_ty", ""), company_id or "other")
        monthly[key] = monthly.get(key, 0) + 1
        if company_id is None:
            continue

        zip_path = raw_dart_dir / "documents" / f"{rcept_no}.zip"
        content = ""
        if zip_path.exists():
            try:
                content = extract_dart_zip(zip_path, rcept_no)
            except (zipfile.BadZipFile, OSError) as e:
                logger.warning("text extraction failed for %s: %s", rcept_no, e)
        report_nm = re.sub(r"\s+", " ", item["report_nm"]).strip()
        rcept_dt = datetime.strptime(item["rcept_dt"], "%Y%m%d").date().isoformat()
        docs[rcept_no] = {
            "doc_id": f"DART_{rcept_no}",
            "source_type": "dart",
            "title": report_nm,
            "content": content,
            "published_at": rcept_dt,
            "url": viewer_url(rcept_no),
            "source_name": item.get("flr_nm"),
            "company_ids": [company_id],
            "corp_code": item.get("corp_code"),
            "rcept_no": rcept_no,
            "report_nm": report_nm,
            "rcept_dt": rcept_dt,
            "pblntf_ty": request.get("pblntf_ty"),
            "content_hash": content_hash(report_nm, content),
            "is_relevant": True,
            "raw_path": _rel(zip_path if zip_path.exists() else path),
            "collected_at": _now(),
        }
    counts = [
        {"month": m, "pblntf_ty": ty, "filer": f, "count": n} for (m, ty, f), n in sorted(monthly.items())
    ]
    return list(docs.values()), counts


def select_audit_originals(
    raw_dart_dir: Path,
    mapping: dict[str, Any],
    big4_corp_codes: set[str],
    month: str,
    corp_cls: list[str],
    per_firm: int,
    seed: int,
    exclude_corrections: bool = True,
) -> list[dict[str, Any]]:
    """Reproducible sample of client audit reports whose originals will be downloaded.

    Filter: filed by a Big4 firm, submitted in `month` (YYYY-MM), not the firm's own filing,
    client class in `corp_cls` (empty = any; Y=유가증권, K=코스닥, N=코넥스, E=기타/비상장).
    Corrections ("[기재정정]...") are excluded by default: filed in `month` but often for an old
    fiscal year. One filing per client (consolidated preferred), then `per_firm` random clients
    per firm with a fixed seed.
    """
    import random

    filers = filer_to_company(mapping)
    by_firm: dict[str, dict[str, dict[str, Any]]] = {}
    for _path, _request, item in iter_market_listings(raw_dart_dir):
        firm = _filer_company(item.get("flr_nm", ""), filers)
        if (
            firm is None
            or item.get("corp_code") in big4_corp_codes
            or (corp_cls and item.get("corp_cls") not in corp_cls)
            or (exclude_corrections and item["report_nm"].lstrip().startswith("["))
            or not item["rcept_dt"].startswith(month.replace("-", ""))
        ):
            continue
        clients = by_firm.setdefault(firm, {})
        current = clients.get(item["corp_code"])
        # Prefer the consolidated report when a client filed both: it covers the group.
        if current is None or ("연결" in item["report_nm"] and "연결" not in current["report_nm"]):
            clients[item["corp_code"]] = {**item, "company_id": firm}

    rng = random.Random(seed)
    sample = []
    for firm in sorted(by_firm):
        candidates = sorted(by_firm[firm].values(), key=lambda x: x["rcept_no"])
        sample.extend(rng.sample(candidates, min(per_firm, len(candidates))))
    return sample
