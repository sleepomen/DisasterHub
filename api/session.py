"""登入、登出、目前是誰。session 放在 HttpOnly cookie，前端不會碰到憑證本身"""
import logging

from fastapi import APIRouter, HTTPException, Request, Response

from api import deps
from api.models import LoginRequest
from services.web import auth

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/login")
async def login(request: LoginRequest, http_request: Request, response: Response):
    if not auth.credentials_configured():
        raise HTTPException(status_code=503, detail="伺服器未設定 ADMIN_USERNAME / ADMIN_PASSWORD，登入已停用")
    key = deps.client_key(http_request)
    wait = deps.login_throttle.retry_after(key)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"登入失敗次數過多，請 {wait} 秒後再試")
    if not auth.check_credentials(request.username, request.password):
        deps.login_throttle.record_failure(key)
        # 不記輸入的帳號：操作員把密碼打進帳號欄時，密碼就會留在 log 裡
        logger.warning("登入失敗：來源 %s", key)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    deps.login_throttle.reset(key)
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=deps.session_manager.issue(request.username),
        max_age=deps.session_manager.max_age,
        httponly=True,
        samesite="lax",
        secure=deps.request_is_https(http_request),
        path="/",
    )
    logger.info("登入成功：%s，來源 %s", request.username, key)
    return {"status": "success", "user": request.username, "expires_in": deps.session_manager.max_age}


@router.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(key=auth.COOKIE_NAME, path="/")
    return {"status": "success"}


# 前端載入時問一次：要不要顯示登入面板、目前是誰
@router.get("/api/me")
async def me(http_request: Request):
    user = deps.current_user(http_request)
    return {
        "authenticated": user is not None,
        "user": user,
        "login_enabled": auth.credentials_configured(),
    }
