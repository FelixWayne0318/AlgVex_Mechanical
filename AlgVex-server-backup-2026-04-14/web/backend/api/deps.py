"""
API Dependencies - Authentication helpers
"""
from fastapi import HTTPException, Request, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from typing import Optional

from core.config import settings

security = HTTPBearer(auto_error=False)


async def get_token_from_request(request: Request) -> Optional[str]:
    """Extract token from cookie or Authorization header"""
    # Try cookie first
    token = request.cookies.get("access_token")
    if token:
        return token

    # Try Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


async def get_current_admin_from_request(request: Request) -> Optional[dict]:
    """Get current admin from request"""
    token = await get_token_from_request(request)
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        email = payload.get("sub")
        if not email:
            return None

        # Check admin whitelist
        if settings.ADMIN_EMAILS and email not in settings.ADMIN_EMAILS:
            return None

        return {
            "email": email,
            "name": payload.get("name", ""),
            "picture": payload.get("picture", ""),
        }
    except JWTError:
        return None


async def get_current_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> dict:
    """Dependency to require admin authentication"""
    user = await get_current_admin_from_request(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
