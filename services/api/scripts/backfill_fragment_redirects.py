import asyncio
import json
import os
import re
import sys

import asyncpg
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from scripts.import_tecd3 import normalize_query

PARENT_LINK_RE = re.compile(r'mdict-parent-link[^>]*href="entry://([^"]+)"')


def _extract_parent_key(raw_html: str) -> str | None:
    m = PARENT_LINK_RE.search(raw_html)
    if not m:
        return None
    return m.group(1).strip() or None


def _get_first_pos(meanings_json: list[dict]) -> str | None:
    for group in meanings_json:
        pos = str(group.get("part_of_speech") or "").strip()
        if pos:
            return pos
    return None


def _build_preview(meanings_json: list[dict]) -> str | None:
    parts: list[str] = []
    for group in meanings_json[:2]:
        for definition in group.get("definitions", [])[:2]:
            meaning = str(definition.get("meaning") or "").strip()
            if meaning:
                parts.append(meaning)
    if not parts:
        return None
    return "；".join(parts)[:180]


async def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("Error: DATABASE_URL not set in .env")
        return

    print("Connecting to database...")
    conn = await asyncpg.connect(db_url)

    print("1. Finding empty fragment entries with parent links...")
    fragments = await conn.fetch("""
        SELECT id, display_headword, source_entry_key, raw_html
        FROM dict_entries
        WHERE entry_kind = 'fragment'
          AND jsonb_array_length(meanings_json) = 0
          AND raw_html LIKE '%mdict-parent-link%'
        ORDER BY id
    """)
    print(f"   Found {len(fragments)} empty fragments with parent links")

    parent_entries_by_key: dict[str, dict] = {}
    lookup_rows: list[tuple] = []
    redirect_rows: list[tuple] = []
    skipped_no_parent = 0
    skipped_no_meanings = 0

    for frag in fragments:
        parent_key = _extract_parent_key(frag["raw_html"])
        if not parent_key:
            skipped_no_parent += 1
            continue

        if parent_key not in parent_entries_by_key:
            row = await conn.fetchrow(
                "SELECT id, display_headword, source_entry_key, meanings_json FROM dict_entries WHERE source_entry_key = $1 AND source = 'tecd3'",
                parent_key,
            )
            parent_entries_by_key[parent_key] = dict(row) if row else None

        parent = parent_entries_by_key[parent_key]
        if parent is None:
            skipped_no_parent += 1
            continue

        meanings = parent["meanings_json"]
        if isinstance(meanings, str):
            meanings = json.loads(meanings)
        if not meanings:
            skipped_no_meanings += 1
            continue

        norm_form = normalize_query(frag["display_headword"])
        if not norm_form:
            continue

        target_pos = _get_first_pos(meanings)
        preview_text = _build_preview(meanings)

        lookup_rows.append((
            "tecd3",
            norm_form,
            frag["display_headword"],
            parent["id"],
            parent["display_headword"],
            target_pos,
            preview_text,
            100,
            "redirect",
            "word",
        ))

        redirect_rows.append((
            "tecd3",
            norm_form,
            parent["source_entry_key"],
            "mdx_link",
        ))

    print(f"   Prepared {len(lookup_rows)} lookup targets, {len(redirect_rows)} redirects")
    print(f"   Skipped: {skipped_no_parent} (parent not found), {skipped_no_meanings} (parent has no meanings)")

    if lookup_rows:
        print("2. Inserting redirect lookup targets...")
        sql = """
        INSERT INTO dict_lookup_targets (
            source, normalized_form, lookup_label, entry_id,
            target_label, target_pos, preview_text, rank, match_kind, lookup_type
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (source, normalized_form, entry_id, match_kind) DO NOTHING
        """
        chunk_size = 5000
        inserted = 0
        for i in range(0, len(lookup_rows), chunk_size):
            chunk = lookup_rows[i : i + chunk_size]
            result = await conn.executemany(sql, chunk)
            inserted += len(chunk)
            print(f"   Inserted {min(i + chunk_size, len(lookup_rows))}/{len(lookup_rows)}")
        print(f"   Done inserting lookup targets (ON CONFLICT DO NOTHING)")

    if redirect_rows:
        print("3. Inserting redirect records...")
        sql = """
        INSERT INTO dict_redirects (source, redirect_key, target_entry_key, redirect_kind)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (source, redirect_key, target_entry_key, redirect_kind) DO NOTHING
        """
        chunk_size = 5000
        for i in range(0, len(redirect_rows), chunk_size):
            await conn.executemany(sql, redirect_rows[i : i + chunk_size])
            print(f"   Inserted {min(i + chunk_size, len(redirect_rows))}/{len(redirect_rows)}")
        print(f"   Done inserting redirects (ON CONFLICT DO NOTHING)")

    await conn.close()
    print("All done!")


if __name__ == "__main__":
    asyncio.run(main())
