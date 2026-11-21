"""Runtime configuration. Everything comes from environment variables (prefix MT_)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MT_", extra="ignore")

    data_dir: Path = Field(default=Path("/data"))
    models_dir: Path = Field(default=Path("/models"))
    tmp_dir: Path = Field(default=Path("/tmp/manga-translator"))  # noqa: S108 - tmpfs in container
    db_filename: str = "manga_translator.sqlite3"

    # secrets
    master_key_file: Path | None = None
    master_key: str | None = None  # tests only; prefer master_key_file

    # upload limits
    max_upload_bytes: int = 25 * 1024 * 1024
    # Whole-request budget (any body, enforced at the ASGI layer before the multipart parser runs).
    # The parser spools file parts to /tmp (tmpfs, charged to the container's memory cgroup), so this
    # must stay well below the api memory limit and the tmpfs size, and <= nginx client_max_body_size.
    max_upload_total_bytes: int = 256 * 1024 * 1024
    max_pages_per_upload: int = 100
    max_pages_per_project: int = 500
    max_image_pixels: int = 40_000_000
    min_image_side: int = 32
    max_image_side: int = 12_000

    # OCR
    ocr_engine: str = "paddle"  # paddle | mock
    ocr_inference_engine: str = "onnxruntime"  # onnxruntime | paddle_static
    ocr_version: str = "PP-OCRv5"
    ocr_det_model: str = "PP-OCRv5_mobile_det"
    ocr_rec_model: str = "PP-OCRv5_mobile_rec"
    ocr_cpu_threads: int = 4
    ocr_textline_orientation: bool = True
    # Detection input size: manga pages are large and small kana vanish when the page is shrunk to the
    # library default (960). "max" bounds the longer side; 1600 keeps typical 1.3k-2k px pages near native.
    ocr_det_limit_side_len: int = 1600
    ocr_det_limit_type: str = "max"  # max | min
    # Second detection pass at this size (0 disables); boxes not overlapping the first pass are added.
    ocr_det_secondary_side_len: int = 960
    ocr_det_thresh: float | None = None
    ocr_det_box_thresh: float | None = None
    ocr_det_unclip_ratio: float | None = None
    manga_ocr_enabled: bool = True
    # auto: manga-ocr on Japanese/unknown regions scoring below manga_ocr_min_confidence;
    # always_ja: on every Japanese/unknown region; off: never (unless the user forces it per page).
    manga_ocr_mode: str = "auto"
    manga_ocr_min_confidence: float = 0.7
    ocr_page_timeout_seconds: int = 300

    # Translation
    max_ai_concurrency: int = 2
    translate_batch_size: int = 12
    max_chars_per_job: int = 200_000
    provider_timeout_seconds: int = 60
    provider_max_response_bytes: int = 2_000_000
    provider_max_retries: int = 3
    mock_providers: bool = False
    endpoint_allowlist: str = ""
    allow_internal_endpoints: bool = False

    # worker
    worker_poll_seconds: float = 1.0
    worker_heartbeat_file: Path = Path("/tmp/manga-translator/worker.heartbeat")  # noqa: S108
    job_max_attempts: int = 3

    # rendering
    font_dir: Path = Path("/usr/share/fonts")
    render_min_font_size: int = 9
    render_max_font_size: int = 72
    # Erase only the text strokes (inpainting) instead of a flat box; MT_RENDER_INPAINT=0 restores the box.
    render_inpaint: bool = True
    # Font files per script; empty = first bundled candidate (Patrick Hand / Noto Sans CJK).
    render_font_latin: str = ""
    render_font_cjk: str = ""
    # Text outline only where the background is not flat (0 = never, 1 = only on complex background, 2 = always).
    render_halo: int = 1
    # Upper bound of the auto font size relative to the original glyph size (CJK glyphs fill their
    # em box, so the measured size is a little under the font size), and an extra factor when the
    # target uses a Latin font, whose x-height is roughly half the em.
    render_glyph_scale: float = 1.25
    render_glyph_scale_latin: float = 1.4
    # Inpainting backend for the erased strokes: "auto" uses LaMa when its model file is present
    # (MT_MODELS_DIR/lama/lama_fp32.onnx), else OpenCV Telea; "lama" / "telea" force one.
    render_inpaint_model: str = "auto"
    # Fetch the LaMa model (≈200 MB, Apache-2.0) on first use instead of `python -m app.render.lama download`.
    lama_auto_download: bool = False

    # export
    export_zip_max_bytes: int = 2 * 1024 * 1024 * 1024

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename

    @property
    def allowlisted_hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.endpoint_allowlist.split(",") if h.strip()}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


def is_test_env() -> bool:
    return os.environ.get("MT_TEST_MODE", "0") == "1"
