from __future__ import annotations

from app.schemas.user_assets.favorites import (
    FavoriteCreateRequest,
    FavoriteDeleteResponse,
    FavoriteListResponse,
    FavoriteResponse,
)
from app.schemas.user_assets.records import (
    RecordCreateRequest,
    RecordListResponse,
    RecordResponse,
    RecordUpdateRequest,
    RecordUpsertResponse,
)
from app.schemas.user_assets.vocabulary import (
    VocabularyCreateRequest,
    VocabularyListResponse,
    VocabularyResponse,
    VocabularyUpdateRequest,
    VocabularyUpsertResponse,
)

__all__ = [
    # Records
    "RecordCreateRequest",
    "RecordUpdateRequest",
    "RecordResponse",
    "RecordListResponse",
    "RecordUpsertResponse",
    # Favorites
    "FavoriteCreateRequest",
    "FavoriteResponse",
    "FavoriteListResponse",
    "FavoriteDeleteResponse",
    # Vocabulary
    "VocabularyCreateRequest",
    "VocabularyUpdateRequest",
    "VocabularyResponse",
    "VocabularyListResponse",
    "VocabularyUpsertResponse",
]
