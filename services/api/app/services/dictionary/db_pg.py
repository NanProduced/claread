"""
PostgreSQL 版本的 TECD3 词典查询实现。

运行时通过 dict_lookup_targets 决定：
- 单候选 -> entry
- 多候选 -> disambiguation
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.database import connection as db_connection
from app.services.dictionary.errors import ServiceUnavailableError


@dataclass(frozen=True)
class EntryRow:
    id: int
    source: str
    source_entry_key: str
    entry_kind: str
    display_headword: str
    base_headword: str | None
    homograph_no: int | None
    phonetic: str | None
    meanings_json: list[dict[str, Any]]
    examples_json: list[dict[str, Any]]
    phrases_json: list[dict[str, Any]]
    sections_json: list[dict[str, Any]]
    raw_html: str | None
    parse_version: str
    exam_tags: list[str]


@dataclass(frozen=True)
class CandidateRow:
    entry_id: int
    normalized_form: str
    lookup_label: str
    target_label: str
    target_pos: str | None
    preview_text: str | None
    rank: int
    match_kind: str
    entry_kind: str
    lookup_type: str
    has_meanings: bool


def _row_to_entry(row: Any) -> EntryRow | None:
    if row is None:
        return None
    return EntryRow(
        id=row["id"],
        source=row["source"],
        source_entry_key=row["source_entry_key"],
        entry_kind=row["entry_kind"],
        display_headword=row["display_headword"],
        base_headword=row["base_headword"],
        homograph_no=row["homograph_no"],
        phonetic=row["phonetic"],
        meanings_json=_coerce_json_list(row["meanings_json"]),
        examples_json=_coerce_json_list(row["examples_json"]),
        phrases_json=_coerce_json_list(row["phrases_json"]),
        sections_json=_coerce_json_list(row["sections_json"]),
        raw_html=row["raw_html"],
        parse_version=row["parse_version"],
        exam_tags=list(row["exam_tags"]) if row["exam_tags"] else [],
    )


def _coerce_json_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    if isinstance(value, list):
        return value
    return list(value)


def _row_to_candidate(row: Any) -> CandidateRow:
    return CandidateRow(
        entry_id=row["entry_id"],
        normalized_form=row["normalized_form"],
        lookup_label=row["lookup_label"],
        target_label=row["target_label"],
        target_pos=row["target_pos"],
        preview_text=row["preview_text"],
        rank=row["rank"],
        match_kind=row["match_kind"],
        entry_kind=row["entry_kind"],
        lookup_type=row.get("lookup_type", "word"),
        has_meanings=row.get("has_meanings", True),
    )


async def fetch_entry(entry_id: int, source: str = "tecd3") -> EntryRow | None:
    if db_connection.DB_POOL is None:
        raise ServiceUnavailableError("Database connection pool not initialized")
    async with db_connection.DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              id, source, source_entry_key, entry_kind,
              display_headword, base_headword, homograph_no, phonetic,
              meanings_json, examples_json, phrases_json, sections_json,
              raw_html, parse_version, exam_tags
            FROM dict_entries
            WHERE source = $1 AND id = $2
            """,
            source,
            entry_id,
        )
        return _row_to_entry(row)


async def lookup_candidates(normalized_form: str, source: str = "tecd3") -> list[CandidateRow]:
    if db_connection.DB_POOL is None:
        raise ServiceUnavailableError("Database connection pool not initialized")
    async with db_connection.DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              t.normalized_form,
              t.lookup_label,
              t.entry_id,
              t.target_label,
              t.target_pos,
              t.preview_text,
              t.rank,
              t.match_kind,
              e.entry_kind,
              t.lookup_type,
              jsonb_array_length(e.meanings_json) > 0 AS has_meanings
            FROM dict_lookup_targets t
            JOIN dict_entries e
              ON e.id = t.entry_id
             AND e.source = t.source
            WHERE t.source = $1
              AND t.normalized_form = $2
            ORDER BY t.rank ASC, t.id ASC
            """,
            source,
            normalized_form,
        )

    candidates: list[CandidateRow] = []
    seen_entry_ids: set[int] = set()
    for row in rows:
        candidate = _row_to_candidate(row)
        if candidate.entry_id in seen_entry_ids:
            continue
        seen_entry_ids.add(candidate.entry_id)
        candidates.append(candidate)
    return candidates

async def lookup_candidates_batch(normalized_forms: list[str], source: str = "tecd3") -> list[CandidateRow]:
    if db_connection.DB_POOL is None or not normalized_forms:
        if db_connection.DB_POOL is None:
            raise ServiceUnavailableError("Database connection pool not initialized")
        return []
    async with db_connection.DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
              t.normalized_form,
              t.lookup_label,
              t.entry_id,
              t.target_label,
              t.target_pos,
              t.preview_text,
              t.rank,
              t.match_kind,
              e.entry_kind,
              t.lookup_type,
              jsonb_array_length(e.meanings_json) > 0 AS has_meanings
            FROM dict_lookup_targets t
            JOIN dict_entries e
              ON e.id = t.entry_id
             AND e.source = t.source
            WHERE t.source = $1
              AND t.normalized_form = ANY($2::text[])
            ORDER BY array_position($2::text[], t.normalized_form), t.rank ASC, t.id ASC
            """,
            source,
            normalized_forms,
        )

    candidates: list[CandidateRow] = []
    seen_combinations = set()
    for row in rows:
        candidate = _row_to_candidate(row)
        key = (candidate.entry_id, candidate.normalized_form)
        if key in seen_combinations:
            continue
        seen_combinations.add(key)
        candidates.append(candidate)
    return candidates
