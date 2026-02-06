from __future__ import annotations

import logging
from typing import Optional, Literal

from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from .auth import verify_user
from . import config as cfg
from .render import (
    ensure_plot_png,
    ensure_export_pdf,
    ensure_export_tiff,
    RenderError,
)

# -----------------------------
# bootstrap (dirs + logging)
# -----------------------------
cfg.ensure_runtime()

logger = logging.getLogger("spatial_site")
if not logger.handlers:
    level = getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    fh = logging.FileHandler(cfg.LOG_FILE, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


# -----------------------------
# FastAPI app
# -----------------------------
app = FastAPI(title="Spatial Website", version="0.3.0")

# Session secret：生产环境请用强随机串（环境变量 SESSION_SECRET）
app.add_middleware(SessionMiddleware, secret_key=cfg.SESSION_SECRET)

# static + templates
app.mount("/static", StaticFiles(directory=str(cfg.PROJECT_ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(cfg.PROJECT_ROOT / "templates"))


# -----------------------------
# auth helpers
# -----------------------------
def require_login(request: Request) -> bool:
    return request.session.get("user") is not None


# -----------------------------
# error handling
# -----------------------------
def _status_from_error_code(code: str) -> int:
    if code in {"BAD_INPUT", "LAYER_NOT_FOUND", "GENE_NOT_FOUND"}:
        return 400
    if code in {"FILE_NOT_FOUND"}:
        return 404
    if code in {"LOCK_TIMEOUT"}:
        return 408
    return 500


@app.exception_handler(RenderError)
def render_error_handler(_: Request, exc: RenderError):
    status = _status_from_error_code(exc.code)
    logger.warning(f"RenderError {exc.code}: {exc.message} | {exc.detail}")
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
    )


# -----------------------------
# routes: entry
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if require_login(request):
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


# -----------------------------
# routes: auth pages
# -----------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if verify_user(username, password):
        request.session["user"] = username
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "用户名或密码错误"},
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# -----------------------------
# routes: pages
# -----------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    last_gene = request.session.get("last_gene")
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "last_gene": last_gene,
            "png_url": f"/img/png/{last_gene}" if last_gene else None,
        },
    )


# -----------------------------
# routes: plot workflow
# -----------------------------
@app.post("/plot", response_class=HTMLResponse)
def plot_action(
    request: Request,
    gene: str = Form(...),
):
    """
    Plot:
    - figures/<level>/png/{gene}.png 存在 => 直接返回
    - 不存在 => 生成默认 DPI PNG，缓存后返回
    """
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    gene = gene.strip()
    if not gene:
        raise RenderError("BAD_INPUT", "gene 不能为空。")

    res = ensure_plot_png(gene=gene)
    request.session["last_gene"] = res.gene

    logger.info(f"plot | gene={res.gene} cache_hit={res.cache_hit} out={res.out_path.name}")

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "last_gene": res.gene,
            "png_url": f"/img/png/{res.gene}",
            "cache_hit": res.cache_hit,
        },
    )


@app.get("/img/png/{gene}")
def serve_plot_png(request: Request, gene: str):
    """页面渲染用：返回 png（inline）。"""
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    gene = gene.strip()
    if not gene:
        raise RenderError("BAD_INPUT", "gene 不能为空。")

    res = ensure_plot_png(gene=gene)
    headers = {"X-Cache-Hit": "1" if res.cache_hit else "0"}
    return FileResponse(res.out_path.as_posix(), media_type="image/png", headers=headers)


# -----------------------------
# routes: export workflow
# -----------------------------
@app.post("/export")
def export_action(
    request: Request,
    gene: str = Form(...),
    export_type: Literal["pdf", "tiff"] = Form(...),
    dpi: Optional[int] = Form(None),
):
    """
    Export:
    - pdf: figures/<level>/pdf/{gene}.pdf
    - tiff: figures/<level>/tiff/{gene}_{dpi}.tiff
    """
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    gene = gene.strip()
    if not gene:
        raise RenderError("BAD_INPUT", "gene 不能为空。")

    if export_type == "pdf":
        res = ensure_export_pdf(gene=gene)
        logger.info(f"export | type=pdf gene={res.gene} cache_hit={res.cache_hit} out={res.out_path.name}")
        return FileResponse(
            res.out_path.as_posix(),
            media_type="application/pdf",
            filename=res.out_path.name,
            headers={"Content-Disposition": f'attachment; filename="{res.out_path.name}"'},
        )

    # tiff
    if dpi is None:
        raise RenderError("BAD_INPUT", "导出 tiff 必须提供 dpi。")
    res = ensure_export_tiff(gene=gene, dpi=int(dpi))
    logger.info(f"export | type=tiff dpi={dpi} gene={res.gene} cache_hit={res.cache_hit} out={res.out_path.name}")
    return FileResponse(
        res.out_path.as_posix(),
        media_type="image/tiff",
        filename=res.out_path.name,
        headers={"Content-Disposition": f'attachment; filename="{res.out_path.name}"'},
    )


# -----------------------------
# routes: health
# -----------------------------
@app.get("/health")
def health():
    return {
        "ok": True,
        "project_root": str(cfg.PROJECT_ROOT),
        "data_level": cfg.DATA_LEVEL,
        "data_dir": str(cfg.DATA_DIR),
        "figure_dir": str(cfg.FIGURE_DIR),
        "png_dir": str(cfg.PNG_DIR),
        "pdf_dir": str(cfg.PDF_DIR),
        "tiff_dir": str(cfg.TIFF_DIR),
        "lock_dir": str(cfg.LOCK_DIR),
        "default_layer": cfg.DEFAULT_LAYER,
        "default_basis": cfg.DEFAULT_BASIS,
        "plot_png_dpi": cfg.PLOT_PNG_DPI,
        "allowed_export_dpi": sorted(list(cfg.ALLOWED_EXPORT_DPI)),
    }
