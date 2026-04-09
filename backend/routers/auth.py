from fastapi import APIRouter, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends
from pydantic import BaseModel
from auth import (
    get_user, create_user, verify_password,
    create_access_token, get_current_user,
)

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    password: str


@router.post("/register")
async def register(req: RegisterRequest):
    if len(req.username) < 3 or len(req.password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Логин от 3 символов, пароль от 6",
        )
    if get_user(req.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь уже существует",
        )
    create_user(req.username, req.password)
    return {"message": "ok"}


@router.post("/login")
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = get_user(form.username)
    if not user or not verify_password(form.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    token = create_access_token(form.username)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
async def me(username: str = Depends(get_current_user)):
    user = get_user(username)
    return {
        "username": username,
        "created_at": user.get("created_at") if user else None,
    }
