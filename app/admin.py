"""Admin cabinet routes: auth + provider key management.

Mounted under ``/admin``; JSON endpoints live under ``/api/auth`` and
``/api/admin`` so the dashboard's existing ``/api/*`` contract is untouched.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.auth import SESSION_COOKIE, SESSION_TTL_DAYS, current_user, hash_password, login, logout, require_admin, verify_password
from app.provider_registry import PROVIDER_DEFS, mask_config
from app.provider_settings import env_config, merge_provider_configs
from app.store import Store

router = APIRouter()

static_dir = Path(__file__).parent / "static"


# ---------------------------------------------------------------------------
# Auth pages & endpoints (no session required)
# ---------------------------------------------------------------------------


@router.get("/admin")
@router.get("/admin/")
def admin_page():
    return FileResponse(static_dir / "admin.html")


@router.get("/admin/login")
def admin_login_page():
    return FileResponse(static_dir / "admin.html")


@router.get("/api/auth/session")
def auth_session(request: Request):
    user = current_user(request)
    has_users = request.app.state.store.count_users() > 0
    return {
        "authenticated": user is not None,
        "username": user["username"] if user else None,
        # First run: no account exists yet -> the UI offers initial setup
        # instead of a plain login form.
        "needs_setup": not has_users,
    }


@router.post("/api/auth/setup")
async def auth_setup(request: Request, response: Response):
    """First-run bootstrap: create the very first admin account."""
    store: Store = request.app.state.store
    if store.count_users() > 0:
        raise HTTPException(status_code=409, detail={"error": "account already exists"})
    body = await _json_body(request)
    return await _create_account_and_login(response, store, body)


@router.post("/api/auth/login")
async def auth_login(request: Request, response: Response):
    store: Store = request.app.state.store
    if store.count_users() == 0:
        raise HTTPException(status_code=409, detail={"error": "no account yet: complete first-time setup"})
    body = await _json_body(request)
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    token = login(store, username, password)
    if token is None:
        raise HTTPException(status_code=401, detail={"error": "invalid credentials"})
    _set_session_cookie(response, token)
    return {"ok": True, "username": username}


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    logout(request.app.state.store, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


async def _create_account_and_login(
    response: Response, store: Store, body: dict[str, Any]
) -> dict[str, Any]:
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if len(username) < 2:
        raise HTTPException(status_code=422, detail={"error": "username too short"})
    if len(password) < 8:
        raise HTTPException(status_code=422, detail={"error": "password must be at least 8 characters"})
    user_id = store.create_user(username, hash_password(password))
    from app.auth import new_session_token, token_hash
    from datetime import datetime, timedelta, timezone

    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)
    store.create_session(token_hash(token), user_id, expires)
    _set_session_cookie(response, token)
    return {"ok": True, "username": username}


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid JSON body"})
    return body if isinstance(body, dict) else {}


# ---------------------------------------------------------------------------
# Account management (session required)
# ---------------------------------------------------------------------------


@router.post("/api/admin/change-password")
async def change_password(request: Request, user: dict = Depends(require_admin)):
    body = await _json_body(request)
    old_password = str(body.get("old_password", ""))
    new_password = str(body.get("new_password", ""))
    store: Store = request.app.state.store
    full_user = store.get_user(user["username"])
    if not full_user or not verify_password(old_password, full_user["password_hash"]):
        raise HTTPException(status_code=403, detail={"error": "wrong current password"})
    if len(new_password) < 8:
        raise HTTPException(status_code=422, detail={"error": "password must be at least 8 characters"})
    store.update_password(full_user["id"], hash_password(new_password))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Provider config CRUD (session required)
# ---------------------------------------------------------------------------


@router.get("/api/admin/providers")
def list_providers(request: Request, user: dict = Depends(require_admin)):
    settings = request.app.state.settings
    store: Store = request.app.state.store
    merged = merge_provider_configs(store, settings)
    providers = []
    for slug, meta in PROVIDER_DEFS.items():
        cfg = merged.get(slug, {})
        providers.append(
            {
                "slug": slug,
                "label": meta["label"],
                "enabled": cfg.get("_enabled", True),
                "overridden": slug in {c["slug"] for c in store.list_provider_configs()},
                "fields": mask_config(slug, cfg),
            }
        )
    return {"providers": providers}


@router.put("/api/admin/providers/{slug}")
async def update_provider(slug: str, request: Request, user: dict = Depends(require_admin)):
    meta = PROVIDER_DEFS.get(slug)
    if meta is None:
        raise HTTPException(status_code=404, detail={"error": "unknown provider"})
    store: Store = request.app.state.store
    settings = request.app.state.settings

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "invalid JSON body"})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "invalid JSON body"})

    field_names = [f["name"] for f in meta["fields"]]
    existing = next((c for c in store.list_provider_configs() if c["slug"] == slug), None)
    stored_cfg: dict[str, Any] = dict(existing["config"]) if existing else {}
    env_cfg = env_config(slug, settings)

    new_cfg: dict[str, Any] = {}
    for name in field_names:
        value = body.get(name)
        if value is None:
            # Field untouched in the form: keep previously stored secret, if any.
            new_cfg[name] = stored_cfg.get(name, env_cfg.get(name) or "")
        elif isinstance(value, str) and value.strip() == "" and _is_secret(meta, name):
            # Empty secret input means "leave unchanged" rather than erase.
            new_cfg[name] = stored_cfg.get(name, "")
        else:
            new_cfg[name] = str(value).strip()

    enabled = bool(body.get("enabled", True)) if "enabled" in body else (existing["enabled"] if existing else True)
    display_name = str(body.get("display_name") or meta["label"])
    store.upsert_provider_config(slug, display_name, enabled, new_cfg)

    # Rebuild the live collector provider list immediately.
    request.app.state.collector.rebuild_providers(store)
    return {"ok": True, "slug": slug}


@router.delete("/api/admin/providers/{slug}")
def reset_provider(slug: str, request: Request, user: dict = Depends(require_admin)):
    """Remove the DB override; the provider falls back to .env defaults."""
    if slug not in PROVIDER_DEFS:
        raise HTTPException(status_code=404, detail={"error": "unknown provider"})
    store: Store = request.app.state.store
    store.delete_provider_config(slug)
    request.app.state.collector.rebuild_providers(store)
    return {"ok": True}


def _is_secret(meta: dict[str, Any], name: str) -> bool:
    return any(f["name"] == name and f.get("secret") for f in meta["fields"])
