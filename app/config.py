# app/config.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Set


# -------------------------
# 基础路径与环境变量工具
# -------------------------
def _env_str(name: str, default: str) -> str:
    """Get env var as string, fallback to default (string)."""
    v = os.getenv(name)
    return v if (v is not None and str(v).strip() != "") else default


def _env_path(name: str, default: Path) -> Path:
    """Get env var as Path safely; if env exists, treat it as path string."""
    return Path(_env_str(name, str(default))).expanduser().resolve()


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(v)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got: {v!r}")


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


# -------------------------
# 项目根目录（以 app/ 的父目录为根）
# -------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]

# 数据层级（你现在是 bin100）
DATA_LEVEL: str = _env_str("DATA_LEVEL", "bin100")

# 数据与图输出目录（强制 Path 化，避免 env var 导致 str / Path 混用）
DATA_DIR: Path = _env_path("DATA_DIR", PROJECT_ROOT / "data" / DATA_LEVEL)
FIGURE_DIR: Path = _env_path("FIG_DIR", PROJECT_ROOT / "figures" / DATA_LEVEL)

# 图文件输出子目录（render.py / main.py 会用到）
PDF_DIR: Path = FIGURE_DIR / "pdf"
PNG_DIR: Path = FIGURE_DIR / "png"
TIFF_DIR: Path = FIGURE_DIR / "tiff"

# 锁目录（用于缓存/并发绘图避免重复）
LOCK_DIR: Path = FIGURE_DIR / "locks"


def ensure_dirs() -> None:
    """Create all required directories."""
    for p in (DATA_DIR, FIGURE_DIR, PDF_DIR, PNG_DIR, TIFF_DIR, LOCK_DIR):
        p.mkdir(parents=True, exist_ok=True)


# -------------------------
# 渲染默认参数（render.py 会引用）
# -------------------------
# 默认使用哪个表达层：你不想丢 counts/lognorm，这里默认 lognorm（可用 env 改）
DEFAULT_LAYER: str = _env_str("DEFAULT_LAYER", "lognorm")

# 默认 basis / spatial key（sq.pl.spatial_scatter 的 basis/spatial_key 取决于你实现）
# 你现有代码里叫 DEFAULT_BASIS，所以这里给一个保守默认值
DEFAULT_BASIS: str = _env_str("DEFAULT_BASIS", "spatial")

# 画 PNG 的默认 DPI
PLOT_PNG_DPI: int = _env_int("PLOT_PNG_DPI", 300)

# 允许用户导出时选择的 DPI（render.py 校验会用）
# 注意：这里用 set[int]，你在 render.py 里用 `in` 判断会很快
ALLOWED_EXPORT_DPI: Set[int] = {150, 300, 600, 1200}


# -------------------------
# 鉴权与会话（main.py/auth.py 会用到）
# -------------------------
# auth.json 路径（你项目根目录下 auth.json）
AUTH_FILE: Path = _env_path("AUTH_FILE", PROJECT_ROOT / "auth.json")

# Session secret：强烈建议生产环境必须提供
# 如果你想本地也强制要求，把下面 fallback 删掉，改成 _require_env("SESSION_SECRET")
SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-secret-change-me")


# -------------------------
# 日志
# -------------------------
LOG_DIR: Path = _env_path("LOG_DIR", PROJECT_ROOT / "logs")
LOG_FILE: Path = LOG_DIR / "app.log"
LOG_LEVEL: str = _env_str("LOG_LEVEL", "INFO")


def ensure_runtime() -> None:
    """Call this once at startup to create folders needed by the app."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ensure_dirs()


# -------------------------
# 其他可选：数据文件扫描（方便你在 main.py 里做下拉列表）
# -------------------------
def list_h5ad_files() -> list[str]:
    """
    Return h5ad file names (not full path) under DATA_DIR.
    Example: ['sham.h5ad', 'MCAO_1d.h5ad', ...]
    """
    if not DATA_DIR.exists():
        return []
    return sorted([p.name for p in DATA_DIR.glob("*.h5ad")])

# 缓存总上限：10 GiB
CACHE_MAX_BYTES: int = int(_env_int("CACHE_MAX_GB", 10) * 1024**3)

# 需要纳入配额统计的目录（locks 不算）
CACHE_DIRS = [PNG_DIR, PDF_DIR, TIFF_DIR]
