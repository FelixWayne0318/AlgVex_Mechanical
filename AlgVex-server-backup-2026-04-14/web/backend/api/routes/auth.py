"""
Authentication Routes - Google OAuth
"""
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from jose import jwt
from datetime import datetime, timedelta
import secrets

from core.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])

# OAuth setup
oauth = OAuth()

oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def create_access_token(data: dict) -> str:
    """Create JWT access token"""
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = data.copy()
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


@router.get("/login")
async def login(request: Request):
    """Initiate Google OAuth login"""
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")

    redirect_uri = settings.GOOGLE_REDIRECT_URI
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback/google")
async def google_callback(request: Request):
    """Handle Google OAuth callback"""
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if not user_info:
            raise HTTPException(status_code=400, detail="Failed to get user info")

        email = user_info.get("email")

        # Check if user is allowed admin
        if settings.ADMIN_EMAILS and email not in settings.ADMIN_EMAILS:
            raise HTTPException(status_code=403, detail="Access denied")

        # Create JWT token
        access_token = create_access_token({
            "sub": email,
            "name": user_info.get("name", ""),
            "picture": user_info.get("picture", ""),
        })

        # Redirect to frontend with token in URL for localStorage extraction.
        # Frontend reads router.query.token → localStorage → Bearer header.
        # NOTE: token briefly visible in URL; frontend immediately does
        # router.replace() to clear it. HttpOnly cookie is also set as a
        # secondary auth channel (deps.py checks cookie first).
        response = RedirectResponse(url=f"https://algvex.com/admin?token={access_token}")
        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        return response

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/me")
async def get_current_user(request: Request):
    """Get current authenticated user info"""
    from api.deps import get_current_admin_from_request

    user = await get_current_admin_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return user


@router.post("/logout")
async def logout(response: Response):
    """Logout - clear session cookie"""
    response.delete_cookie(key="access_token")
    return {"success": True, "message": "Logged out"}
