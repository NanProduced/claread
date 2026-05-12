import asyncio
import os
import re
import json
import asyncpg
from dotenv import load_dotenv
import sys

# 把 import_tecd3 里的 normalize_query 引进来，保证入库词形归一化一致
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from scripts.import_tecd3 import normalize_query

# 使用 phrase_templates.py 作为 canonicalization 的唯一来源，
# 避免维护两套规则导致分叉。
from app.services.dictionary.phrase_templates import canonicalize_dictionary_phrase as canonicalize_template

async def main():
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL not set in .env")
        return

    print("Connecting to database...")
    conn = await asyncpg.connect(db_url)

    print("1. Updating existing fragment entries to lookup_type = 'phrase'...")
    await conn.execute("""
        UPDATE dict_lookup_targets t
        SET lookup_type = 'phrase'
        FROM dict_entries e
        WHERE t.entry_id = e.id
          AND e.entry_kind = 'fragment'
          AND t.lookup_type = 'word';
    """)

    print("2. Extracting hidden phrases from phrases_json...")
    rows = await conn.fetch("SELECT id, display_headword, phrases_json FROM dict_entries WHERE jsonb_array_length(phrases_json) > 0")

    lookup_rows = []

    for row in rows:
        entry_id = row['id']
        phrases_list = json.loads(row['phrases_json']) if isinstance(row['phrases_json'], str) else row['phrases_json']

        for item in phrases_list:
            phrase_text = item.get('phrase')
            if not phrase_text:
                continue

            preview = item.get('meaning')
            preview_text = f"{phrase_text} {preview}"[:180] if preview else phrase_text

            # (a) Literal phrase
            norm_form = normalize_query(phrase_text)
            if norm_form:
                lookup_rows.append((
                    'tecd3', norm_form, phrase_text, entry_id, row['display_headword'],
                    None, preview_text, 10, 'phrase', 'phrase'
                ))

            # (b) Template phrase
            template_text = canonicalize_template(phrase_text)
            if template_text != phrase_text:
                norm_template = normalize_query(template_text)
                if norm_template and norm_template != norm_form:
                    lookup_rows.append((
                        'tecd3', norm_template, template_text, entry_id, row['display_headword'],
                        None, preview_text, 11, 'phrase_template', 'phrase'
                    ))

    print("3. Generating template phrases for existing fragments (like 'be there for sb.')...")
    fragment_rows = await conn.fetch("SELECT id, display_headword, meanings_json FROM dict_entries WHERE entry_kind = 'fragment'")

    for row in fragment_rows:
        entry_id = row['id']
        headword = row['display_headword']

        template_text = canonicalize_template(headword)
        if template_text != headword:
            norm_template = normalize_query(template_text)
            norm_headword = normalize_query(headword)

            if norm_template and norm_template != norm_headword:
                preview_text = headword
                meanings = json.loads(row['meanings_json']) if isinstance(row['meanings_json'], str) else row['meanings_json']
                if meanings and isinstance(meanings, list) and meanings[0].get('definitions'):
                    meaning = meanings[0]['definitions'][0].get('meaning')
                    if meaning:
                        preview_text = f"{headword} {meaning}"[:180]

                lookup_rows.append((
                    'tecd3', norm_template, template_text, entry_id, row['display_headword'],
                    None, preview_text, 11, 'phrase_template', 'phrase'
                ))

    if lookup_rows:
        print(f"Ready to insert {len(lookup_rows)} new phrase targets. Inserting...")
        sql = """
        INSERT INTO dict_lookup_targets (
            source, normalized_form, lookup_label, entry_id,
            target_label, target_pos, preview_text, rank, match_kind, lookup_type
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (source, normalized_form, entry_id, match_kind) DO NOTHING
        """
        # Execute in chunks
        chunk_size = 5000
        for i in range(0, len(lookup_rows), chunk_size):
            await conn.executemany(sql, lookup_rows[i:i+chunk_size])
        print("Done inserting new phrase lookup targets!")
    else:
        print("No hidden phrases or templates to insert.")

    await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
