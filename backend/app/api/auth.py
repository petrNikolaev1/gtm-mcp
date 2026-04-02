"""Auth API — signup, token management, profile."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db import get_session
from app.models.user import MCPUser, MCPApiToken
from app.models.project import Company
from app.auth.middleware import generate_api_token
from app.auth.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str
    name: str
    password: str


class SignupResponse(BaseModel):
    user_id: int
    api_token: str  # shown ONCE
    message: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/signup", response_model=SignupResponse)
async def signup(req: SignupRequest, session: AsyncSession = Depends(get_session)):
    # Check if email already exists
    existing = await session.execute(
        select(MCPUser).where(MCPUser.email == req.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # Hash password
    import bcrypt
    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    # Create user
    user = MCPUser(email=req.email, name=req.name, password_hash=password_hash)
    session.add(user)
    await session.flush()

    # Create default company for user
    company = Company(name=f"{req.name}'s Company")
    session.add(company)
    await session.flush()

    # Generate API token
    raw_token, prefix, hashed = generate_api_token()
    token = MCPApiToken(
        user_id=user.id,
        token_prefix=prefix,
        token_hash=hashed,
        name="default",
    )
    session.add(token)
    await session.commit()

    return SignupResponse(
        user_id=user.id,
        api_token=raw_token,
        message="Account created. Save your API token — it won't be shown again.",
    )


@router.post("/login")
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    """Login with email + password. Returns a fresh API token."""
    import bcrypt

    result = await session.execute(
        select(MCPUser).where(MCPUser.email == req.email)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check password (if user has one)
    if user.password_hash:
        if not bcrypt.checkpw(req.password.encode(), user.password_hash.encode()):
            raise HTTPException(status_code=401, detail="Invalid email or password")
    else:
        # Legacy account without password — set it now
        user.password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()

    # Generate fresh API token
    raw_token, prefix, hashed = generate_api_token()
    token = MCPApiToken(
        user_id=user.id,
        token_prefix=prefix,
        token_hash=hashed,
        name="login",
    )
    session.add(token)
    await session.commit()

    return {
        "user_id": user.id,
        "api_token": raw_token,
        "message": "Logged in. Token generated.",
    }


class MeResponse(BaseModel):
    user_id: int
    email: str
    name: str
    integrations: list[dict]


@router.get("/me", response_model=MeResponse)
async def get_me(
    user: MCPUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    from app.models.integration import MCPIntegrationSetting
    result = await session.execute(
        select(MCPIntegrationSetting).where(
            MCPIntegrationSetting.user_id == user.id
        )
    )
    integrations = [
        {
            "name": i.integration_name,
            "connected": i.is_connected,
            "info": i.connection_info,
        }
        for i in result.scalars().all()
    ]

    return MeResponse(
        user_id=user.id,
        email=user.email,
        name=user.name,
        integrations=integrations,
    )
