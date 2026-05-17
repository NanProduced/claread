"""词典服务。"""

from __future__ import annotations

import re
from typing import Any

from app.services.dictionary.errors import WordNotFoundError, ServiceUnavailableError
from app.services.dictionary.providers import Tecd3Provider
from app.services.dictionary.schemas import DictionaryLookupRequest


class DictionaryService:
    _ALIAS_MAP: dict[str, str] = {
        "u.s.": "us",
        "u.k.": "uk",
        "e.g.": "eg",
        "i.e.": "ie",
        "prof.": "prof",
        "dr.": "dr",
        "mr.": "mr",
        "mrs.": "mrs",
        "ms.": "ms",
        "vs.": "vs",
        "etc.": "etc",
    }
    _TRANSLATION_MAP = str.maketrans(
        {
            "’": "'",
            "‘": "'",
            "“": '"',
            "”": '"',
            "⁰": "0",
            "¹": "1",
            "²": "2",
            "³": "3",
            "⁴": "4",
            "⁵": "5",
            "⁶": "6",
            "⁷": "7",
            "⁸": "8",
            "⁹": "9",
        }
    )

    def __init__(self) -> None:
        self._provider = Tecd3Provider()

    async def lookup(self, request: DictionaryLookupRequest) -> dict[str, Any]:
        request.query = self._normalize(request.query)
        try:
            return await self._provider.fetch(request)
        except ValueError:
            raise WordNotFoundError(f"Word not found: {request.query}") from None

    async def lookup_entry(self, entry_id: int) -> dict[str, Any]:
        try:
            return await self._provider.fetch_entry(entry_id)
        except ValueError:
            raise WordNotFoundError(f"Entry not found: {entry_id}") from None

    def normalize_query(self, word: str) -> str:
        return self._normalize(word)

    def _normalize(self, word: str) -> str:
        normalized = word.strip().translate(self._TRANSLATION_MAP).lower()
        normalized = normalized.replace("·", "").replace("•", "")
        normalized = re.sub(r"(?<=\S)\s+([0-9]+)$", r"\1", normalized)
        normalized = self._ALIAS_MAP.get(normalized, normalized)
        normalized = re.sub(r"^[^\w]+|[^\w]+$", "", normalized)
        normalized = self._ALIAS_MAP.get(normalized, normalized)
        return normalized


_service: DictionaryService | None = None


def get_service() -> DictionaryService:
    global _service
    if _service is None:
        _service = DictionaryService()
    return _service
