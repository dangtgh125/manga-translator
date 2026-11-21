"""Pydantic API contracts (see PLAN.md §4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Direction = Literal["vertical", "horizontal", "unknown"]
SourceLang = Literal["ja", "zh-Hans", "zh-Hant", "unknown"]
ReadingDirection = Literal["rtl", "ltr"]
ProviderKind = Literal["google", "openai", "anthropic", "responses_compatible", "mock"]
JobScope = Literal["all", "page"]
JobStep = Literal["ocr", "translate", "render", "all"]
# done: every page succeeded; partial: some pages failed, others succeeded (see progress.failed_pages);
# failed: every page failed or the job itself errored.
JobStatus = Literal["queued", "running", "done", "partial", "failed", "cancelled"]
MaskMode = Literal["auto", "box", "none"]

Point = tuple[float, float]


class Box(BaseModel):
    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)


class GlossaryEntry(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=300)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_lang: str = Field(default="vi", min_length=2, max_length=16)
    reading_direction: ReadingDirection = "rtl"
    glossary: list[GlossaryEntry] = Field(default_factory=list, max_length=2000)


class ProjectUpdate(BaseModel):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=200)
    target_lang: str | None = Field(default=None, min_length=2, max_length=16)
    reading_direction: ReadingDirection | None = None
    glossary: list[GlossaryEntry] | None = Field(default=None, max_length=2000)
    settings: dict[str, Any] | None = None


class PageOut(BaseModel):
    id: str
    project_id: str
    position: int
    display_name: str
    width: int
    height: int
    format: str
    status: str
    steps: dict[str, Any]
    error: str | None
    source_lang_override: str | None
    detected_lang: str | None
    lang_uncertain: bool
    has_render: bool
    render_stale: bool
    # Regions whose translation is outdated (target language/glossary/source changed); >0 means the
    # page needs re-translation before its render can be trusted.
    translation_stale_count: int = 0
    translation_stale: bool = False
    render_report: dict[str, Any] | None
    version: int
    created_at: str
    updated_at: str


class ProjectOut(BaseModel):
    id: str
    name: str
    target_lang: str
    reading_direction: ReadingDirection
    glossary: list[GlossaryEntry]
    settings: dict[str, Any]
    version: int
    created_at: str
    updated_at: str
    pages: list[PageOut] = Field(default_factory=list)


class ProjectSummary(BaseModel):
    id: str
    name: str
    target_lang: str
    page_count: int
    created_at: str
    updated_at: str


class PageReorder(BaseModel):
    page_ids: list[str] = Field(min_length=1)


class PageUpdate(BaseModel):
    version: int
    source_lang_override: SourceLang | None = None
    clear_source_lang_override: bool = False


class RegionOut(BaseModel):
    id: str
    page_id: str
    polygon: list[Point]
    direction: Direction
    reading_order: int
    source_lang: str
    lang_confidence: float
    lang_uncertain: bool
    ocr_text: str
    ocr_confidence: float
    ocr_engine: str
    source_text: str
    source_edited: bool
    polygon_edited: bool = False
    direction_edited: bool = False
    source_lang_edited: bool = False
    translated_text: str
    final_text: str
    final_edited: bool
    final_lang: str = ""
    translation_provider: str
    translation_stale: bool
    translation_flags: list[str]
    review_status: str
    mask_mode: MaskMode
    mask_box: Box | None
    text_box: Box | None
    font_size: float | None
    lines: list[dict[str, Any]]
    origin: str
    version: int


class RegionCreate(BaseModel):
    polygon: list[Point] = Field(min_length=3, max_length=64)
    direction: Direction = "unknown"
    source_lang: SourceLang = "unknown"
    source_text: str = Field(default="", max_length=5000)
    reading_order: int | None = None


class RegionUpdate(BaseModel):
    version: int
    polygon: list[Point] | None = Field(default=None, min_length=3, max_length=64)
    direction: Direction | None = None
    reading_order: int | None = None
    source_lang: SourceLang | None = None
    source_text: str | None = Field(default=None, max_length=5000)
    final_text: str | None = Field(default=None, max_length=5000)
    review_status: Literal["pending", "approved", "flagged"] | None = None
    mask_mode: MaskMode | None = None
    mask_box: Box | None = None
    text_box: Box | None = None
    font_size: float | None = Field(default=None, ge=4, le=200)

    @field_validator("source_text", "final_text")
    @classmethod
    def _no_control_chars(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return "".join(ch for ch in v if ch == "\n" or ch == "\t" or ch.isprintable())


class RegionReorder(BaseModel):
    region_ids: list[str] = Field(min_length=1)


IDEMPOTENCY_KEY_MAX_LENGTH = 128


class JobCreate(BaseModel):
    scope: JobScope = "all"
    page_id: str | None = None
    step: JobStep = "all"
    provider_profile_id: str | None = None
    # Opaque client token, scoped per project (see repo.create_job); same limit applies to the header.
    idempotency_key: str | None = Field(default=None, max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
    options: dict[str, Any] = Field(default_factory=dict)


class RetryFailedBody(BaseModel):
    """Body of POST /api/jobs/{id}/retry-failed: re-run only the pages that failed in that job."""

    step: JobStep | None = None  # default: the source job's step
    provider_profile_id: str | None = None  # default: the source job's provider
    idempotency_key: str | None = Field(default=None, max_length=IDEMPOTENCY_KEY_MAX_LENGTH)
    options: dict[str, Any] = Field(default_factory=dict)  # merged over the source job's options


class JobOut(BaseModel):
    id: str
    project_id: str
    scope: JobScope
    page_id: str | None
    step: JobStep
    status: JobStatus = Field(
        description="queued | running | done (all pages ok) | partial (some pages failed, see "
        "progress.failed_pages) | failed (all pages failed or job error) | cancelled"
    )
    # total/done/failed/steps/pages{page_id: {status, step, error}}/failed_pages[{page_id, position,
    # display_name, step, error}]/estimate/retry_of
    progress: dict[str, Any]
    error: str | None
    attempts: int
    cancel_requested: bool
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class ProviderProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: ProviderKind
    model_id: str = Field(default="", max_length=200)
    endpoint: str | None = Field(default=None, max_length=500)
    options: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=20000)


class ProviderProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    model_id: str | None = Field(default=None, max_length=200)
    endpoint: str | None = Field(default=None, max_length=500)
    options: dict[str, Any] | None = None


class ProviderSecretSet(BaseModel):
    secret: str = Field(min_length=1, max_length=20000)


class ProviderProfileOut(BaseModel):
    id: str
    name: str
    provider: ProviderKind
    model_id: str
    endpoint: str | None
    options: dict[str, Any]
    has_secret: bool
    created_at: str
    updated_at: str


class ProviderTestRequest(BaseModel):
    profile_id: str | None = None
    provider: ProviderKind | None = None
    model_id: str | None = Field(default=None, max_length=200)
    endpoint: str | None = Field(default=None, max_length=500)
    options: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=20000)


class ProviderTestResult(BaseModel):
    ok: bool
    provider: str
    model_id: str
    message: str
    models: list[str] = Field(default_factory=list)
    data_recipient: str = ""


class CostEstimate(BaseModel):
    regions: int
    characters: int
    provider: str
    note: str


class HealthOut(BaseModel):
    status: str
    version: str
    ocr_engine: str
    secrets_available: bool
    worker_alive: bool | None = None
