"""Username/password registration, login, logout and session restoration."""

import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from agent.mysql_storage import mysql_storage
from api_view.agent_loader import agent_loader
from api_view.auth import (
    COOKIE_NAME, cookie_secure, hash_password, require_same_origin_request,
    require_user, start_session, token_hash, verify_password,
)

router = APIRouter(prefix="/auth", dependencies=[Depends(require_same_origin_request)])
# An unknown username still performs a password derivation to avoid an obvious timing shortcut.
_DUMMY_HASH = f"scrypt${secrets.token_hex(16)}${secrets.token_hex(32)}"
_password_slots = asyncio.Semaphore(2)


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        import re
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{2,32}", value):
            raise ValueError("用户名需为 2–32 位字母、数字、下划线或短横线")
        return value


@router.post("/register", status_code=201)
async def register(body: Credentials, request: Request, response: Response):
    async with _password_slots:
        encoded = await asyncio.to_thread(hash_password, body.password)
    if not await mysql_storage.create_user(body.username, encoded):
        raise HTTPException(409, "用户名已被注册")
    result = await start_session(body.username, request, response)
    agent_loader.schedule_preparation(body.username)
    return result


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response):
    user = await mysql_storage.get_user(body.username)
    async with _password_slots:
        valid = await asyncio.to_thread(verify_password, body.password,
                                        user["password_hash"] if user else _DUMMY_HASH)
    if not user or not valid:
        raise HTTPException(401, "用户名或密码错误")
    result = await start_session(user["username"], request, response)
    agent_loader.schedule_preparation(user["username"])
    return result


@router.get("/me")
async def current_user(response: Response, username: str = Depends(require_user)):
    response.headers["Cache-Control"] = "no-store"
    agent_loader.schedule_preparation(username)
    return {"user": {"username": username}}


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        user = await mysql_storage.get_auth_user(token_hash(token))
        await mysql_storage.delete_auth_session(token_hash(token))
        if user and not await mysql_storage.has_auth_sessions(user["username"]):
            await agent_loader.logout_preparation(user["username"])
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, secure=cookie_secure(request), samesite="lax")
    response.headers["Cache-Control"] = "no-store"
