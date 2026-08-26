"""图片 source URL override 的请求/响应 DTO。

输入边界：``url`` 无长度上限、不 trim、不校验 scheme、
不调用 loadability validator；唯一写入层拒绝是 U+0000（PostgreSQL text
不能存储），必须在到达 asyncpg/数据库之前以确定性 422 拒绝。

技术合同修正（Finding #2）：PostgreSQL text 为 UTF-8 且 lone surrogate
（U+D800..U+DFFF）不可持久化，原“除 U+0000 外均可原样持久化”不成立；
`block_id` 与 `url` 均需在 Pydantic/service 双层以 422 拒绝不可表示文本。
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

URL_NULL_CHARACTER_REASON = "url_null_character_not_persistable"
BLOCK_ID_NULL_CHARACTER_REASON = "block_id_null_character_not_persistable"
# 仅空串与 NUL 在 Pydantic 层拒绝；lone surrogate 原样透传至 service 的
# 真实 surrogate 扫描（_ensure_postgres_text），避免 marker 改写原始输入
URL_NOT_REPRESENTABLE_REASON = "url_not_representable_as_postgres_text"
BLOCK_ID_NOT_REPRESENTABLE_REASON = "block_id_not_representable_as_postgres_text"


def _check_block_id_before(value: object) -> object:
    if not isinstance(value, str):
        return value
    if value == "":
        raise PydanticCustomError("string_too_short", "String should have at least 1 character")
    if "\u0000" in value:
        raise PydanticCustomError(BLOCK_ID_NULL_CHARACTER_REASON, BLOCK_ID_NULL_CHARACTER_REASON)
    return value


def _check_url_before(value: object) -> object:
    if not isinstance(value, str):
        return value
    if "\u0000" in value:
        raise PydanticCustomError(URL_NULL_CHARACTER_REASON, URL_NULL_CHARACTER_REASON)
    return value


class ImageSourceOverrideUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stable_document_id: UUID
    block_id: str
    inline_ordinal: int | None = Field(default=None, ge=0)
    url: str

    @field_validator("block_id", mode="before")
    @classmethod
    def _validate_block_id_before(cls, value: object) -> object:
        return _check_block_id_before(value)

    @field_validator("url", mode="before")
    @classmethod
    def _validate_url_before(cls, value: object) -> object:
        return _check_url_before(value)


class ImageSourceOverrideMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    last_event_sequence: int = Field(ge=0)
