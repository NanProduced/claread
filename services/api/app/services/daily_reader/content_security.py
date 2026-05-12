"""Content security check layer for Daily Reader article pipeline.

Uses WeChat msgSecCheck API to scan article content before processing.
"""

from __future__ import annotations

import logging

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


async def check_content_security(title: str, text: str) -> dict:
    access_token = await _get_wechat_access_token()
    if not access_token:
        logger.warning("WeChat access token unavailable, skipping content security check")
        return {"suggest": "review", "label": 100, "trace_id": "", "detail": [], "skipped": True}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.weixin.qq.com/wxa/msg_sec_check",
                params={"access_token": access_token},
                json={
                    "content": text[:2500],
                    "version": 2,
                    "scene": 3,
                    "openid": _get_system_openid(),
                    "title": title[:100],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("WeChat msgSecCheck API error: %s", e)
        return {"suggest": "review", "label": 100, "trace_id": "", "detail": [], "error": str(e)}

    result = data.get("result", {})
    return {
        "suggest": result.get("suggest", "review"),
        "label": result.get("label", 100),
        "trace_id": data.get("trace_id", ""),
        "detail": data.get("detail", []),
    }


def is_content_safe(sec_check_result: dict) -> bool:
    if sec_check_result.get("skipped"):
        return True
    suggest = sec_check_result.get("suggest", "review")
    label = sec_check_result.get("label", 100)
    if suggest == "risky":
        return False
    if suggest == "review" and label >= 20000:
        return False
    return True


async def _get_wechat_access_token() -> str | None:
    settings = get_settings()
    app_id = settings.wechat_app_id
    app_secret = settings.wechat_app_secret
    if not app_id or not app_secret:
        return None

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.weixin.qq.com/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": app_id,
                    "secret": app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if "access_token" in data:
                return data["access_token"]
            logger.warning("WeChat token error: %s", data.get("errmsg", "unknown"))
            return None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("WeChat token request failed: %s", e)
        return None


def _get_system_openid() -> str:
    settings = get_settings()
    return settings.daily_reader_admin_openid or "system"
