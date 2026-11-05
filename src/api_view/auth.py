"""Password hashing and server-side login sessions shared by Web APIs."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response

from agent.mysql_storage import mysql_storage

COOKIE_NAME = "deepagent_session"
SESSION_SECONDS = 7 * 24 * 60 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**17, r=8, p=1,
                            maxmem=256 * 1024 * 1024, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if len(salt) != 16 or len(expected) != 32:
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**17, r=8, p=1,
                                maxmem=256 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def cookie_secure(request: Request) -> bool:
    return request.url.scheme == "https" or os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"


async def require_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        raise HTTPException(401, "请先登录")
    user = await mysql_storage.get_auth_user(token_hash(token))
    if user is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    return user["username"]


def require_same_origin_request(request: Request) -> None:
    # 非简单请求头配合受限 CORS，拒绝跨站表单发起带 Cookie 的写操作。
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("X-Requested-With") != "DeepAgent":
        raise HTTPException(403, "无效请求，请刷新页面后重试")


async def require_thread(thread_id: str, username: str = Depends(require_user)) -> str:
    if not await mysql_storage.thread_belongs_to(thread_id, username):
        raise HTTPException(404, "会话不存在")
    return username


async def start_session(username: str, request: Request, response: Response) -> dict:
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        await mysql_storage.delete_auth_session(token_hash(previous))
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=SESSION_SECONDS)
    await mysql_storage.create_auth_session(token_hash(token), username, expiry)
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=cookie_secure(request), samesite="lax", path="/api")
    response.headers["Cache-Control"] = "no-store"
    return {"user": {"username": username}}
