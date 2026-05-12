"""
微信 API 封装。

调用微信 auth.code2Session 接口。
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class WeChatAPIError(Exception):
    """微信 API 调用失败"""

    def __init__(self, errcode: int, errmsg: str) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"WeChat API error {errcode}: {errmsg}")


class WeChatSession(NamedTuple):
    """微信 code2Session 返回"""

    openid: str
    session_key: str
    unionid: str | None


async def code2session(code: str) -> WeChatSession:
    """
    调用微信 auth.code2Session 接口，通过 code 换取 session。

    Args:
        code: wx.login() 返回的 code

    Returns:
        WeChatSession(openid, session_key, unionid)

    Raises:
        WeChatAPIError: 微信接口返回错误
    """
    settings = get_settings()

    if not settings.wechat_app_id or not settings.wechat_app_secret:
        raise WeChatAPIError(-1, "WeChat app credentials not configured")

    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": settings.wechat_app_id,
        "secret": settings.wechat_app_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.warning("WeChat network error: %s", e)
        raise WeChatAPIError(-3, f"Network error: {e}") from e

    # 非 200 状态码（5xx 网关错误等）直接抛 502
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning("WeChat HTTP error: %s [%s]", e, e.response.status_code)
        raise WeChatAPIError(-2, f"HTTP {e.response.status_code}: upstream error") from e

    # 响应体解析失败（HTML / 错误页等）
    try:
        data = resp.json()
    except Exception:
        logger.warning("WeChat non-JSON response: %s", resp.text[:200])
        raise WeChatAPIError(-2, "Invalid JSON response from WeChat") from None

    errcode = data.get("errcode")

    if errcode and errcode != 0:
        logger.warning("WeChat code2session failed: %s", data)
        raise WeChatAPIError(errcode, data.get("errmsg", "unknown error"))

    openid: str | None = data.get("openid")
    session_key: str | None = data.get("session_key")
    unionid: str | None = data.get("unionid")

    if not openid or not session_key:
        raise WeChatAPIError(-1, "Missing openid or session_key in response")

    return WeChatSession(openid=openid, session_key=session_key, unionid=unionid)
