"""
业务 Session 管理。

基于 PostgreSQL 的 session 存储，支持创建、验证、失效。
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.config.settings import get_settings
from app.database import connection as db_connection


def _generate_default_display_name() -> str:
    """生成默认昵称 Claread_xxxx"""
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"Claread_{suffix}"


def _stable_lock_key(openid: str) -> int:
    """
    为 advisory lock 计算稳定的 BIGINT key。

    使用 SHA256 digest 前 8 字节转 BIGINT，保证跨进程稳定性。
    Python hash(openid) 在不同进程/重启动态下不可靠。
    """
    digest = hashlib.sha256(openid.encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big") & 0x7FFFFFFFFFFFFFFF  # 保证为正 BIGINT


@dataclass(frozen=True)
class SessionInfo:
    """已验证的 session 信息"""

    user_id: UUID
    session_id: UUID
    expires_at: datetime
    client_platform: str


def _hash_token(token: str) -> str:
    """计算 session token 的 SHA-256 hexdigest。"""
    return hashlib.sha256(token.encode()).hexdigest().lower()


async def get_or_create_user_by_wechat(
    openid: str,
    unionid: str | None,
    auth_payload: dict[str, Any],
) -> UUID:
    """
    根据微信 openid 查找或创建用户。

    使用 pg_advisory_xact_lock 串行化同一 openid 的并发登录请求，
    消除 SELECT-then-INSERT 竞态。在 lock 内：
    1. 查 user_identities 找现成 identity
    2. 不存在则创建 user + identity

    Args:
        openid: 微信 openid
        unionid: 微信 unionid（可能为 None）
        auth_payload: 微信返回的原始 payload（session_key 等）

    Returns:
        user_id UUID
    """
    if db_connection.DB_POOL is None:
        raise RuntimeError("Database pool not initialized")

    lock_key = _stable_lock_key(openid)

    async with db_connection.DB_POOL.acquire() as conn:
        # 事务级 advisory lock，事务结束自动释放
        await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

        # 查找现有 identity（已持有锁，不会有竞态）
        row = await conn.fetchrow(
            """
            SELECT user_id FROM user_identities
            WHERE provider = 'wechat_miniprogram' AND provider_user_id = $1
            """,
            openid,
        )

        if row is not None:
            return row["user_id"]  # type: ignore[no-any-return]

        # 创建新用户（注册时生成默认昵称 Claread_xxxx）
        default_name = _generate_default_display_name()
        user_id: UUID = await conn.fetchval(
            """
            INSERT INTO users (display_name, metadata_json)
            VALUES ($1, '{}'::jsonb)
            RETURNING id
            """,
            default_name,
        )

        # 创建 identity（已持有锁，unique constraint 只起兜底作用）
        await conn.execute(
            """
            INSERT INTO user_identities
                (user_id, provider, provider_user_id, unionid, app_id, auth_payload_json)
            VALUES ($1, 'wechat_miniprogram', $2, $3, $4, $5)
            """,
            user_id,
            openid,
            unionid,
            get_settings().wechat_app_id or None,
            json.dumps(auth_payload),
        )

        return user_id


async def create_session(
    user_id: UUID,
    provider: str = "wechat_miniprogram",
    provider_user_id: str | None = None,
    auth_payload: dict[str, Any] | None = None,
    client_platform: str = "wechat_miniprogram",
    device_id: str | None = None,
    app_version: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, datetime]:
    """
    创建业务 session。

    生成随机 token，计算 SHA-256 hexdigest，存入 user_sessions。

    Args:
        user_id: 用户 ID
        provider: 认证 provider
        provider_user_id: provider 侧用户 ID
        auth_payload: 认证附加数据
        client_platform: 客户端平台
        device_id: 设备 ID
        app_version: 小程序版本
        ip_address: 客户端 IP

    Returns:
        (token明文, expires_at)
    """
    if db_connection.DB_POOL is None:
        raise RuntimeError("Database pool not initialized")

    settings = get_settings()
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.auth_session_expiry_days)  # noqa: UP017

    async with db_connection.DB_POOL.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_sessions
                (user_id, session_token_hash, client_platform, device_id,
                 device_name, app_version, ip_address, status, expires_at,
                 refresh_expires_at, metadata_json)
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', $8, $9, '{}'::jsonb)
            """,
            user_id,
            token_hash,
            client_platform,
            device_id,
            None,  # device_name
            app_version,
            ip_address,
            expires_at,
            expires_at + timedelta(days=settings.auth_session_expiry_days),
        )

    return token, expires_at


async def validate_session(token: str) -> SessionInfo | None:
    """
    验证 session token。

    查 user_sessions，确认 status='active' 且未过期。
    自动更新 last_seen_at。

    Args:
        token: session token 明文

    Returns:
        SessionInfo 或 None（无效/过期）
    """
    if db_connection.DB_POOL is None:
        logger.warning("validate_session: database pool not initialized, returning None")
        return None

    token_hash = _hash_token(token)
    now = datetime.now(timezone.utc)  # noqa: UP017

    async with db_connection.DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT user_id, id, expires_at, client_platform, status
            FROM user_sessions
            WHERE session_token_hash = $1
            """,
            token_hash,
        )

        if row is None:
            return None

        if row["status"] != "active":
            return None

        if row["expires_at"] < now:
            # 标记为过期
            await conn.execute(
                "UPDATE user_sessions SET status = 'expired' WHERE id = $1",
                row["id"],
            )
            return None

        # 更新 last_seen_at
        await conn.execute(
            "UPDATE user_sessions SET last_seen_at = NOW() WHERE id = $1",
            row["id"],
        )

        return SessionInfo(
            user_id=row["user_id"],
            session_id=row["id"],
            expires_at=row["expires_at"],
            client_platform=row["client_platform"],
        )


async def revoke_session(token: str) -> bool:
    """
    主动失效 session（logout）。

    Args:
        token: session token 明文

    Returns:
        True 找到并失效，False 未找到
    """
    if db_connection.DB_POOL is None:
        logger.warning("revoke_session: database pool not initialized, returning False")
        return False

    token_hash = _hash_token(token)

    async with db_connection.DB_POOL.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE user_sessions
            SET status = 'revoked', revoked_at = NOW()
            WHERE session_token_hash = $1 AND status = 'active'
            """,
            token_hash,
        )

    return "UPDATE 1" in result
