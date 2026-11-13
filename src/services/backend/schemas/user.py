"""Pydantic schemas for User management, authentication, and session contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="Unique username")
    is_admin: bool = Field(default=False, description="Whether user has administrative privileges")


class UserCreate(UserBase):
    password: str = Field(..., min_length=4, description="Plaintext password to be securely hashed")


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=50)
    password: str | None = Field(default=None, min_length=4)
    is_admin: bool | None = None


class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class LoginResponse(BaseModel):
    success: bool
    token: str
    username: str
    is_admin: bool
    expires_in_seconds: int


class SessionInfoResponse(BaseModel):
    authenticated: bool
    username: str | None = None
    is_admin: bool = False
    expires_at: str | None = None


class MessageResponse(BaseModel):
    message: str
    success: bool = True
