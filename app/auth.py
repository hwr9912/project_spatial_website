import json
from pathlib import Path
from typing import Dict

from . import config as cfg
from .render import RenderError


def _load_auth_json() -> Dict[str, str]:
    """
    从服务器上的明文 auth.json 读取用户信息
    返回：{username: password}
    """
    p: Path = cfg.AUTH_FILE

    if not p.exists():
        raise RenderError(
            "AUTH_FILE_MISSING",
            "账号文件不存在",
            detail=str(p),
        )

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RenderError(
            "AUTH_FILE_BAD",
            "账号文件不是合法 JSON",
            detail=str(e),
        )

    users = data.get("users")
    if not isinstance(users, list):
        raise RenderError(
            "AUTH_FILE_BAD",
            "auth.json 格式错误：缺少 users 列表",
        )

    out: Dict[str, str] = {}
    for u in users:
        if not isinstance(u, dict):
            continue
        username = str(u.get("username", "")).strip()
        password = str(u.get("password", ""))
        if username:
            out[username] = password

    return out


def verify_user(username: str, password: str) -> bool:
    """
    登录校验函数（供 main.py 调用）
    """
    users = _load_auth_json()
    return username in users and users[username] == password
