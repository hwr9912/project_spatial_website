# app/render.py
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

import scanpy as sc
import squidpy as sq
import matplotlib.pyplot as plt

from . import config as cfg
from .cache import prune_cache_if_needed

ExportKind = Literal["pdf", "tiff"]


@dataclass(frozen=True)
class RenderResult:
    gene: str
    out_path: Path
    cache_hit: bool


@dataclass
class RenderError(Exception):
    code: str
    message: str
    detail: Optional[str] = None


# -----------------------------
# utils
# -----------------------------
def _safe_gene(gene: str) -> str:
    """
    你要求文件名用“基因名”：
    - 保留字母数字、._-
    - 其他字符替换为 _
    """
    gene = (gene or "").strip()
    if not gene:
        raise RenderError("BAD_INPUT", "gene 不能为空。")
    return "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in gene)


def _pick_default_library() -> str:
    """
    默认 library 的选择策略：
    1) 环境变量 DEFAULT_LIB（例如 sham / MCAO_1d）
    2) DATA_DIR 下第一个 .h5ad 文件（按文件名排序）
    """
    env = os.getenv("DEFAULT_LIB")
    if env and env.strip():
        return env.strip()

    if not cfg.DATA_DIR.exists():
        raise RenderError("FILE_NOT_FOUND", "DATA_DIR 不存在。", detail=str(cfg.DATA_DIR))

    files = sorted(cfg.DATA_DIR.glob("*.h5ad"))
    if not files:
        raise RenderError("FILE_NOT_FOUND", "DATA_DIR 下没有任何 .h5ad 文件。", detail=str(cfg.DATA_DIR))

    return files[0].stem  # filename without suffix


def _adata_path(library: str) -> Path:
    p = cfg.DATA_DIR / f"{library}.h5ad"
    if not p.exists():
        raise RenderError("FILE_NOT_FOUND", f"数据文件不存在：{library}.h5ad", detail=str(p))
    return p


class _FileLock:
    """
    基于 O_EXCL 的简易文件锁，避免并发重复绘图。
    注意：锁目录 cfg.LOCK_DIR 由 cfg.ensure_runtime() 创建；这里也做兜底 mkdir。
    """

    def __init__(self, lock_path: Path, timeout_s: float = 600.0, poll_s: float = 0.2):
        self.lock_path = lock_path
        self.timeout_s = timeout_s
        self.poll_s = poll_s
        self._fd: Optional[int] = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, f"pid={os.getpid()} time={time.time()}\n".encode("utf-8"))
                return
            except FileExistsError:
                if time.time() - start > self.timeout_s:
                    raise RenderError(
                        "LOCK_TIMEOUT",
                        "当前任务排队超时（可能同一张图正在被生成）。",
                        detail=str(self.lock_path),
                    )
                time.sleep(self.poll_s)

    def release(self) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
        finally:
            self._fd = None
            try:
                self.lock_path.unlink(missing_ok=True)
            except Exception:
                pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


def _load_adata(*, library: str, layer: str) -> sc.AnnData:
    adata = sc.read(_adata_path(library).as_posix())

    # layer="X" 表示直接用 adata.X
    if layer != "X":
        if layer not in adata.layers:
            raise RenderError(
                "LAYER_NOT_FOUND",
                f"layer 不存在：{layer}",
                detail=f"available_layers={list(adata.layers.keys())}",
            )
        adata = adata.copy()
        adata.X = adata.layers[layer]
    return adata


def _check_gene_exists(adata: sc.AnnData, gene: str) -> None:
    gene_in_var = gene in adata.var_names
    gene_in_obs = gene in adata.obs.columns
    gene_in_raw = (adata.raw is not None) and (gene in adata.raw.var_names)
    if not (gene_in_var or gene_in_obs or gene_in_raw):
        raise RenderError(
            "GENE_NOT_FOUND",
            f"基因不存在：{gene}",
            detail=f"in_var={gene_in_var}, in_obs={gene_in_obs}, in_raw={gene_in_raw}",
        )


def _default_point_size(n_obs: int) -> float:
    # 你的数据 8-9 万 spots，30 左右更合适
    if n_obs <= 30_000:
        return 50.0
    if n_obs <= 80_000:
        return 30.0
    if n_obs <= 150_000:
        return 18.0
    return 10.0


def _draw(
    *,
    library: str,
    gene: str,
    layer: str,
    basis: str,
    out_path: Path,
    fmt: Literal["png", "pdf", "tiff"],
    dpi: Optional[int] = None,
) -> None:
    """
    真正绘图并保存。
    注意：清理缓存（prune）应在调用 _draw 前完成，避免“刚写完又被清掉”的竞态。
    """
    adata = _load_adata(library=library, layer=layer)
    _check_gene_exists(adata, gene)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(1, 1, figsize=(5, 5))
    try:
        size = _default_point_size(adata.n_obs)

        # squidpy spatial scatter
        sq.pl.spatial_scatter(
            adata,
            color=gene,
            spatial_key=basis,
            library_key="library",
            library_id=[library],
            img=False,
            shape="square",
            size=size,
            alpha=1.0,
            na_color="lightgray",
            frameon=False,
            legend_loc=None,
            cmap="viridis",
            ax=ax,
            fig=fig,
        )

        # pdf 非栅格化；png/tiff 可栅格化减小体积
        ax.set_rasterized(fmt in ("png", "tiff"))

        if fmt == "pdf":
            fig.savefig(out_path.as_posix(), bbox_inches="tight")
        else:
            if dpi is None or int(dpi) <= 0:
                raise RenderError("BAD_INPUT", "dpi 必须为正整数。", detail=f"dpi={dpi}")
            fig.savefig(out_path.as_posix(), dpi=int(dpi), bbox_inches="tight", format=fmt)
    finally:
        plt.close(fig)


# -----------------------------
# public APIs
# -----------------------------
def ensure_plot_png(
    *,
    gene: str,
    library: Optional[str] = None,
    layer: str = cfg.DEFAULT_LAYER,
    basis: str = cfg.DEFAULT_BASIS,
    dpi: int = cfg.PLOT_PNG_DPI,
) -> RenderResult:
    """
    plot 工作流：
    1) figures/<level>/png/{gene}.png
    2) 有 -> 直接返回
    3) 无 -> 生成 png（默认 dpi=cfg.PLOT_PNG_DPI），缓存后返回
    """
    gene = _safe_gene(gene)
    library = library or _pick_default_library()

    out_path = cfg.PNG_DIR / f"{gene}.png"
    lock_path = cfg.LOCK_DIR / f"{gene}.plot_png.lock"

    if out_path.exists():
        return RenderResult(gene=gene, out_path=out_path, cache_hit=True)

    with _FileLock(lock_path):
        if out_path.exists():
            return RenderResult(gene=gene, out_path=out_path, cache_hit=True)

        # 写入前：缓存配额清理（总量 > 10GB 则删旧文件）
        prune_cache_if_needed()

        _draw(
            library=library,
            gene=gene,
            layer=layer,
            basis=basis,
            out_path=out_path,
            fmt="png",
            dpi=dpi,
        )
        return RenderResult(gene=gene, out_path=out_path, cache_hit=False)


def ensure_export_pdf(
    *,
    gene: str,
    library: Optional[str] = None,
    layer: str = cfg.DEFAULT_LAYER,
    basis: str = cfg.DEFAULT_BASIS,
) -> RenderResult:
    """
    非栅格化导出（pdf）：
    1) figures/<level>/pdf/{gene}.pdf
    2) 有 -> 返回
    3) 无 -> 生成 pdf（不 rasterize），缓存后返回
    """
    gene = _safe_gene(gene)
    library = library or _pick_default_library()

    out_path = cfg.PDF_DIR / f"{gene}.pdf"
    lock_path = cfg.LOCK_DIR / f"{gene}.export_pdf.lock"

    if out_path.exists():
        return RenderResult(gene=gene, out_path=out_path, cache_hit=True)

    with _FileLock(lock_path):
        if out_path.exists():
            return RenderResult(gene=gene, out_path=out_path, cache_hit=True)

        prune_cache_if_needed()

        _draw(
            library=library,
            gene=gene,
            layer=layer,
            basis=basis,
            out_path=out_path,
            fmt="pdf",
            dpi=None,
        )
        return RenderResult(gene=gene, out_path=out_path, cache_hit=False)


def ensure_export_tiff(
    *,
    gene: str,
    dpi: int,
    library: Optional[str] = None,
    layer: str = cfg.DEFAULT_LAYER,
    basis: str = cfg.DEFAULT_BASIS,
) -> RenderResult:
    """
    栅格化导出（tiff）：
    1) figures/<level>/tiff/{gene}_{dpi}.tiff
    2) 有 -> 返回
    3) 无 -> 生成指定 dpi 的 tiff，缓存后返回
    """
    gene = _safe_gene(gene)
    library = library or _pick_default_library()

    if int(dpi) not in cfg.ALLOWED_EXPORT_DPI:
        raise RenderError(
            "BAD_INPUT",
            "不支持的 dpi 选项。",
            detail=f"allowed={sorted(list(cfg.ALLOWED_EXPORT_DPI))}, got={dpi}",
        )

    out_path = cfg.TIFF_DIR / f"{gene}_{int(dpi)}.tiff"
    lock_path = cfg.LOCK_DIR / f"{gene}.export_tiff_{int(dpi)}.lock"

    if out_path.exists():
        return RenderResult(gene=gene, out_path=out_path, cache_hit=True)

    with _FileLock(lock_path):
        if out_path.exists():
            return RenderResult(gene=gene, out_path=out_path, cache_hit=True)

        prune_cache_if_needed()

        _draw(
            library=library,
            gene=gene,
            layer=layer,
            basis=basis,
            out_path=out_path,
            fmt="tiff",
            dpi=int(dpi),
        )
        return RenderResult(gene=gene, out_path=out_path, cache_hit=False)
