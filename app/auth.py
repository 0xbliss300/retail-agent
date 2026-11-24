"""HTTP 登录和业务接口共享的身份依赖。"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response

from app.schemas import AccountResponse, LoginRequest
from app.storage import accounts

router = APIRouter(prefix="/auth")
COOKIE_NAME = "retailops_session"
SessionCookie = Annotated[str | None, Cookie(alias=COOKIE_NAME)]


def current_account(
    response: Response,
    token: SessionCookie = None,
    expected_user_id: Annotated[str | None, Header(alias="X-RetailOps-Account")] = None,
) -> AccountResponse:
    account = accounts.get_account(token) if token else None
    # 浏览器标签页可能仍显示切换账号前的页面。这个值只用于拒绝过时请求。
    if account is None or (expected_user_id is not None and expected_user_id != account["user_id"]):
        raise HTTPException(
            status_code=401,
            detail={"error": "LOGIN_REQUIRED", "message": "请重新登录。"},
        )
    response.headers["Cache-Control"] = "no-store"
    return AccountResponse(**account)


CurrentAccount = Annotated[AccountResponse, Depends(current_account)]


def _user_id(account: CurrentAccount) -> str:
    return account.user_id


AuthenticatedUser = Annotated[str, Depends(_user_id)]


@router.post("/login")
def login(
    credentials: LoginRequest, request: Request, response: Response, token: SessionCookie = None,
) -> AccountResponse:
    result = accounts.start_session(credentials.email, credentials.password, token)
    if result is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "INVALID_CREDENTIALS", "message": "邮箱或密码错误。"},
        )
    account, session_token = result
    response.set_cookie(
        COOKIE_NAME, session_token, max_age=accounts.SESSION_MAX_AGE,
        httponly=True, samesite="strict", secure=request.url.scheme == "https",
    )
    response.headers["Cache-Control"] = "no-store"
    return AccountResponse(**account)


@router.get("/me")
def me(account: CurrentAccount) -> AccountResponse:
    return account


@router.post("/logout", dependencies=[Depends(current_account)])
def logout(response: Response, token: Annotated[str, Cookie(alias=COOKIE_NAME)]) -> dict:
    accounts.end_session(token)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict")
    return {"ok": True}
