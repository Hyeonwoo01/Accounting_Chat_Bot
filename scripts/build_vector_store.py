"""Day 5: DART filings -> chunks table + persistent vector store (guide 7-1). Batch only.

Default is a dry run: chunk counts and an embedding cost estimate, no API calls.
`--run` embeds. Re-runs are idempotent: a filing whose chunks are unchanged is skipped, a filing
whose extracted text changed is re-chunked and its old vectors are deleted first, and vectors
missing from the store (e.g. an interrupted run) are added.

    python scripts/build_vector_store.py            # estimate only
    python scripts/build_vector_store.py --run
    python scripts/build_vector_store.py --run --rebuild   # chunk metadata changed (text unchanged)
"""

from __future__ import annotations

import argparse
import csv
import logging
import time
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from src.common.config import (
    PROJECT_ROOT, get_big4_corp_codes, get_embedding_model, get_openai_api_key, get_vectordb_path,
    load_company_mapping, load_settings, mask_secret,
)
from src.database.db_manager import connect, fetch_chunk_texts, fetch_documents, init_db, replace_chunks
from src.rag.document_processor import Chunk, chunk_document, find_boilerplate

log = logging.getLogger("build_vector_store")
AUDIT_SAMPLE = PROJECT_ROOT / "evaluation" / "audit_originals_sample.csv"


def firm_names() -> dict[str, str]:
    """company_id -> Korean legal name (fallback filer name when flr_nm is missing)."""
    return {c["company_id"]: (c.get("kr_entities") or [{}])[0].get("name") or c["company_id"]
            for c in load_company_mapping()["companies"]}


def client_names(path: Path = AUDIT_SAMPLE) -> dict[str, str]:
    """rcept_no -> audited company name (DART corp_name) for the collected audit-report originals."""
    if not path.exists():
        log.warning("%s not found: client names fall back to the report's addressee line", path)
        return {}
    with path.open(encoding="utf-8-sig") as f:
        return {r["rcept_no"]: r["corp_name"] for r in csv.DictReader(f) if r.get("corp_name")}


def plan(docs: list[dict], cfg: dict, conn) -> tuple[dict[str, list[Chunk]], dict[str, list[Chunk]], int]:
    """(all chunks per doc, docs whose chunks differ from the chunks table, boilerplate sentence count)."""
    codes, names, clients = get_big4_corp_codes(), firm_names(), client_names()
    boiler = find_boilerplate([d["content"] for d in docs if d.get("corp_code") not in codes],
                              cfg["boilerplate_min_docs"], cfg["boilerplate_min_chars"])
    every, changed = {}, {}
    for d in docs:
        chunks = chunk_document(d, codes, names, cfg["chunk_size"], cfg["chunk_overlap"],
                                client_names=clients, boilerplate=boiler,
                                min_section_chars=cfg["min_section_chars"])
        if not chunks:
            continue
        every[d["doc_id"]] = chunks
        if fetch_chunk_texts(conn, d["doc_id"]) != [c.text for c in chunks]:
            changed[d["doc_id"]] = chunks
    return every, changed, len(boiler)


def report(every: dict[str, list[Chunk]], changed: dict[str, list[Chunk]], cfg: dict) -> None:
    roles = Counter(c.metadata["filing_role"] for cs in every.values() for c in cs)
    total_chars = sum(len(c.text) for cs in every.values() for c in cs)
    new_chars = sum(len(c.text) for cs in changed.values() for c in cs)
    tokens = new_chars / cfg["chars_per_token"]
    print(f"filings with text: {len(every)}  chunks: {sum(map(len, every.values()))}  "
          f"(own={roles['own']}, audit_client={roles['audit_client']})  chars={total_chars:,}")
    print(f"to (re)embed: filings={len(changed)}  chunks={sum(map(len, changed.values()))}  "
          f"~tokens={tokens:,.0f} (chars/{cfg['chars_per_token']}, rough)")
    price = cfg.get("embedding_price_per_1m")
    if price is None:
        print("embedding cost: settings.rag.embedding_price_per_1m is not set — check the price list first")
    else:
        print(f"embedding cost estimate: ${tokens / 1e6 * price:.4f} (model {get_embedding_model()})")
    print("Not counted: vectors missing from the store for unchanged filings (added on --run).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", help="embed and write (costs money)")
    ap.add_argument("--rebuild", action="store_true",
                    help="re-embed every filing (needed when only chunk METADATA changed, e.g. client names)")
    args = ap.parse_args()
    cfg = load_settings()["rag"]

    with connect() as conn:
        init_db(conn)
        docs = [d for d in fetch_documents(conn, "dart") if (d.get("content") or "").strip()]
        every, changed, n_boiler = plan(docs, cfg, conn)
        if args.rebuild:  # unchanged text is detected via the chunks table; metadata is not compared
            changed = dict(every)
        report(every, changed, cfg)
        unnamed = sum(1 for cs in every.values() if cs[0].metadata["filing_role"] == "audit_client"
                      and not cs[0].metadata["client_name"])
        print(f"boilerplate sentences removed from audit reports: {n_boiler} kinds "
              f"(>= {cfg['boilerplate_min_docs']} reports)  audit reports without client name: {unnamed}")
        if not args.run:
            print("\nDry run. Add --run to embed.")
            return

        from langchain_openai import OpenAIEmbeddings

        from src.rag.vector_store import add_chunks, delete_document, open_store

        model = get_embedding_model()
        store = open_store(get_vectordb_path(), OpenAIEmbeddings(model=model, api_key=get_openai_api_key()), model)
        started, added = time.time(), 0
        try:
            for doc_id in changed:
                delete_document(store, doc_id)
            for i, (doc_id, chunks) in enumerate(every.items(), 1):
                added += add_chunks(store, chunks)
                if doc_id in changed:  # vectors first, then the table: an interrupted run is redone next time
                    replace_chunks(conn, doc_id, [vars(c) for c in chunks])
                    conn.commit()
                if i % 20 == 0:
                    log.info("%d/%d filings, %d chunks embedded", i, len(every), added)
        except Exception as exc:  # noqa: BLE001 — report masked, keep what is stored
            log.error("stopped: %s", mask_secret(str(exc)))
            raise SystemExit(1) from None
        print(f"done: embedded {added} chunks in {time.time() - started:.0f}s -> {get_vectordb_path()}")


if __name__ == "__main__":
    main()
