"""
ir/models.py — Blueprint IR 数据契约（v3.1.2）

按 tech_design §2.2 实现全套 Pydantic 模型。
使用 Pydantic v2 语法。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, model_validator


# ── 枚举定义 ─────────────────────────────────────────────────────────────────

class ArticleType(str, Enum):
    NEWS = "news"
    ANALYSIS = "analysis"
    RESEARCH = "research"
    POLICY = "policy"
    TUTORIAL = "tutorial"
    OPINION = "opinion"
    UNKNOWN = "unknown"


class UserType(str, Enum):
    PROFESSIONAL = "professional"
    GENERAL = "general"


class Platform(str, Enum):
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_MOMENTS = "wechat_moments"


class NarrativeStructure(str, Enum):
    PYRAMID_ARGUMENT = "pyramid_argument"
    PROBLEM_SOLUTION_ACTION = "problem_solution_action"
    CHRONOLOGICAL_TIMELINE = "chronological_timeline"
    BEFORE_AFTER_COMPARISON = "before_after_comparison"
    THOUGHT_JOURNEY = "thought_journey"
    LIN_STYLE_EXPLAINER = "lin_style_explainer"
    PYRAMID_RESEARCH_PRO = "pyramid_research_pro"
    ENTITY_RELATION_MAP = "entity_relation_map"
    CONSENSUS_DISAGREEMENT_MAP = "consensus_disagreement_map"
    MULTI_SOURCE_TIMELINE = "multi_source_timeline"


class CardType(str, Enum):
    COVER = "cover"
    SECTION = "section"
    DATA = "data"
    TIMELINE = "timeline"
    SUMMARY = "summary"


class VisualType(str, Enum):
    TEXT_WITH_ICON = "text_with_icon"
    DATA_HIGHLIGHT = "data_highlight"
    TIMELINE_VERTICAL = "timeline_vertical"
    COMPARISON_TABLE = "comparison_table"
    FLOW_DIAGRAM = "flow_diagram"
    DATA_CHART = "data_chart"
    ENTITY_GRAPH = "entity_graph"
    COVER_HERO = "cover_hero"


class BCheckStatus(str, Enum):
    PASSED = "passed"
    DEGRADED = "degraded"
    REJECTED = "rejected"
    PENDING = "pending"


class RiskLevel(str, Enum):
    SAFE = "safe"
    BORDERLINE = "borderline"
    BLOCKED = "blocked"


class SourceMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class SourceAuthority(str, Enum):
    AUTHORITATIVE = "authoritative"
    MAINSTREAM_MEDIA = "mainstream_media"
    PROFESSIONAL_BLOG = "professional_blog"
    GENERAL_WEB = "general_web"
    USER_GENERATED = "user_generated"


class OutputFormat(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUTO = "auto"


class ArtifactType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class TerminalStatus(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"
    L1_DEGRADED = "l1_degraded"
    L2_DEGRADED = "l2_degraded"
    L3_DEGRADED = "l3_degraded"


# ── 基础数据结构 ─────────────────────────────────────────────────────────────

class Source(BaseModel):
    """单个输入信源。"""
    source_id: str
    raw_text: str
    source_name: str = ""
    url: Optional[str] = None
    title: str = ""
    publish_time: Optional[str] = None
    authority: SourceAuthority = SourceAuthority.GENERAL_WEB


class DuplicateCluster(BaseModel):
    """语义重复信源簇。"""
    cluster_id: str
    member_source_ids: list[str] = Field(default_factory=list)
    representative_source_id: str = ""
    reason: str = ""


class Disagreement(BaseModel):
    """多信源事实/时间/数字/立场冲突。"""
    disagreement_id: str
    disagreement_type: str = "factual_disagreement"
    source_views: list[dict[str, Any]] = Field(default_factory=list)
    resolution: str = "preserve_disagreement"


class SourceBundle(BaseModel):
    """输入源数据包，兼容单源与多源。"""
    mode: SourceMode = SourceMode.SINGLE
    text: str = ""
    source_id: str = "single"
    source_url: Optional[str] = None
    sources: list[Source] = Field(default_factory=list)
    duplicate_clusters: list[DuplicateCluster] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    multi_source_meta: dict[str, Any] = Field(default_factory=dict)
    char_count: int = 0

    @model_validator(mode="after")
    def normalize_sources_and_counts(self) -> "SourceBundle":
        if not self.sources:
            self.sources = [Source(
                source_id=self.source_id or "single",
                raw_text=self.text,
                url=self.source_url,
                source_name=self.source_id or "single",
            )]
        if not self.text:
            self.text = "\n\n".join(s.raw_text for s in self.sources if s.raw_text)
        if self.mode == SourceMode.SINGLE and len(self.sources) > 1:
            self.mode = SourceMode.MULTI
        if self.char_count == 0:
            self.char_count = sum(len(s.raw_text) for s in self.sources) if self.sources else len(self.text)
        return self


class RouterDecision(BaseModel):
    """Agent 1 Router 输出"""
    article_type: ArticleType = ArticleType.UNKNOWN
    user_type: UserType = UserType.GENERAL
    platform: Platform = Platform.XIAOHONGSHU
    narrative_structure: NarrativeStructure = NarrativeStructure.PYRAMID_ARGUMENT
    output_format_hint: OutputFormat = OutputFormat.IMAGE
    risk_level: RiskLevel = RiskLevel.SAFE
    risk_reason: str = ""
    skip_reason: str = ""  # TEXT_TOO_SHORT / TEXT_TOO_LONG
    style_hint: str = ""  # 风格 ID 提示


class SourceOffset(BaseModel):
    """事实在原文中的字符位置。"""
    chunk_id: str = ""
    char_start: int = -1
    char_end: int = -1
    text: str = ""


class ChunkRef(BaseModel):
    """ContextIndex 中的一段原文引用。"""
    chunk_id: str
    char_start: int = 0
    char_end: int = 0
    text: str = ""
    section_title: Optional[str] = None


class ContextIndex(BaseModel):
    """Agent 2 输出的共享上下文索引，供 Worker A/B 与 Gate 1 使用。"""
    source_id: str = "single"
    full_outline: str = ""
    chunks: list[ChunkRef] = Field(default_factory=list)
    chunk_summaries: dict[str, str] = Field(default_factory=dict)

    def get_chunk(self, chunk_id: str) -> Optional[ChunkRef]:
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        return None

    def get_chunks_for_claim(self, claim: "Claim") -> list[ChunkRef]:
        chunk_ids = claim.source_chunk_ids or []
        chunks = [c for c in (self.get_chunk(cid) for cid in chunk_ids) if c]
        return chunks or self.chunks[:1]

    def slice_around_offset(self, offset: SourceOffset | None, radius: int = 200) -> str:
        if not offset:
            return ""
        chunk = self.get_chunk(offset.chunk_id)
        if not chunk:
            return ""
        start = max(0, offset.char_start - radius)
        end = min(len(chunk.text), max(offset.char_end, offset.char_start) + radius)
        return chunk.text[start:end]


class FactElement(BaseModel):
    """事实元素（数字/时间/实体）"""
    text: str                       # 原文中的精确文字
    element_type: str               # "number" | "date" | "entity" | "percentage"
    context_50: str = ""            # 前后各 25 字的上下文（evidence span）
    char_start: int = -1
    char_end: int = -1
    source_offset: Optional[SourceOffset] = None
    normalized_text: Optional[str] = None

    @model_validator(mode="after")
    def backfill_source_offset(self) -> "FactElement":
        if self.source_offset is None and self.char_start >= 0 and self.char_end >= self.char_start:
            self.source_offset = SourceOffset(
                chunk_id="",
                char_start=self.char_start,
                char_end=self.char_end,
                text=self.text,
            )
        return self


class Claim(BaseModel):
    """从原文提取的核心声明"""
    claim_text: str
    evidence_span: str = ""         # 原文引用片段（≤100字）
    evidence_spans: list[dict[str, str]] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    disagreement_refs: list[str] = Field(default_factory=list)
    fact_elements: list[FactElement] = Field(default_factory=list)
    importance: float = 0.5         # 0.0-1.0
    source_chunk_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_source_refs(self) -> "Claim":
        if not self.source_refs:
            self.source_refs = ["single"]
        if self.evidence_span and not self.evidence_spans:
            self.evidence_spans = [{"source_id": self.source_refs[0], "text": self.evidence_span}]
        return self


class ContentTree(BaseModel):
    """Agent 2 内容理解输出"""
    source_bundle: SourceBundle
    claims: list[Claim] = Field(default_factory=list)
    card_budget: int = 8            # PRD 4.3.2 动态计算结果（3-15）
    card_slots: list[dict[str, Any]] = Field(default_factory=list)  # 卡片槽位规划
    outline: str = ""               # 文章结构摘要
    context_index: Optional[ContextIndex] = None


class CardContent(BaseModel):
    """Worker A 文案输出"""
    title: str = Field(max_length=18)
    body: str = Field(max_length=80)
    items: list[str] = Field(default_factory=list)
    data_label: str = ""            # 数据卡大字
    data_desc: str = ""             # 数据卡说明
    timeline_time: str = ""         # 时间线时间字段
    title_warning: bool = False     # 标题党警告（v3.1.3 启用）
    fact_usages: list[FactElement] = Field(default_factory=list)


class VerificationFailure(BaseModel):
    """Worker B 的结构化核验失败。"""
    element: str
    element_type: str = "entity"
    error_type: str = "not_in_source"
    card_field: str = "body"
    source_candidates: list[str] = Field(default_factory=list)


class BCheck(BaseModel):
    """Worker B 事实核验结果"""
    status: BCheckStatus = BCheckStatus.PENDING
    verified_elements: list[str] = Field(default_factory=list)
    failed_elements: list[str] = Field(default_factory=list)
    failures: list[VerificationFailure] = Field(default_factory=list)
    retry_count: int = 0


class VisualSpec(BaseModel):
    """Worker C 视觉结构规范"""
    visual_type: VisualType = VisualType.TEXT_WITH_ICON
    card_type: CardType = CardType.SECTION
    structured_data: dict[str, Any] = Field(default_factory=dict)
    # structured_data 结构示例：
    # timeline: {"nodes": [{"time": "2020", "event": "..."}]}
    # comparison_table: {"rows": [...], "headers": [...]}
    # flow_diagram: {"nodes": [...], "edges": [...]}
    # data_chart: {"type": "pie|bar|line", "labels": [...], "values": [...]}


class StyleTokens(BaseModel):
    """Worker D 风格令牌"""
    style_id: str = "clean_business"
    primary_color: str = "#FF6B6B"
    secondary_color: str = "#FF8E53"
    accent_color: str = "#FFB347"
    background_color: str = "#FFF8F0"
    text_color: str = "#1A1A1A"
    font_size_base: int = 26        # px
    border_radius: int = 20         # px
    card_shadow: str = "0 2px 16px rgba(0,0,0,0.06)"
    cover_gradient: str = "linear-gradient(135deg, #FF6B6B 0%, #FF8E53 50%, #FFB347 100%)"
    summary_gradient: str = "linear-gradient(135deg, #667EEA 0%, #764BA2 100%)"


class Card(BaseModel):
    """Blueprint 中的单张卡片（完整数据）"""
    card_index: int = 0
    content: CardContent
    visual_spec: VisualSpec
    b_check: BCheck = Field(default_factory=BCheck)
    source_claim_index: int = -1    # 对应 ContentTree.claims 中的索引
    suggested_type: CardType = CardType.SECTION
    claim_refs: list[str] = Field(default_factory=list)
    fact_usages: list[FactElement] = Field(default_factory=list)
    is_text_only_fallback: bool = False


class Blueprint(BaseModel):
    """核心 Blueprint IR，流水线各节点交互的中间表示"""
    blueprint_id: str = ""
    source_bundle: SourceBundle
    router_decision: RouterDecision
    content_tree: ContentTree
    context_index: Optional[ContextIndex] = None
    style_tokens: StyleTokens
    output_format: OutputFormat = OutputFormat.IMAGE
    cards: list[Card] = Field(default_factory=list)
    multi_source_meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    pipeline_version: str = "v3.1.2"

    @model_validator(mode="after")
    def validate_card_count(self) -> "Blueprint":
        if len(self.cards) > 0 and not (3 <= len(self.cards) <= 15):
            # 软约束：记录但不报错，让下游处理
            pass
        if not self.multi_source_meta and self.source_bundle.mode == SourceMode.MULTI:
            self.multi_source_meta = self.source_bundle.multi_source_meta
        return self


class RenderArtifact(BaseModel):
    """Tool 1/Tool 2 渲染输出。"""
    output_path: str
    artifact_type: ArtifactType = ArtifactType.IMAGE
    file_size_kb: int = 0
    render_time_s: float = 0.0
    blueprint_id: str = ""
    width: int = 1080
    height: int = 0
    card_count: int = 0
    duration_sec: Optional[float] = None
    thumbnail_path: Optional[str] = None
    audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    terminal_status: TerminalStatus = TerminalStatus.PASSED
    degrade_format: bool = False
    degrade_reason: Optional[str] = None


# ── v3.1.3 Gate 相关模型 ──────────────────────────────────────────────────────

class FailedFact(BaseModel):
    """Gate 1 faithfulness 定位到的失败事实。"""
    card_id: str = ""
    card_field: str = ""
    original_text: str = ""
    hallucinated_text: str = ""
    error_summary: str = ""


class Gate1DimScore(BaseModel):
    """Gate 1 单维度评分"""
    dimension: str                   # "completeness" | "faithfulness" | "card_quality" | "audience_fit"
    score: float = 0.0               # 0-100
    threshold: float = 80.0
    passed: bool = False
    reason: str = ""
    retry_target: str = ""           # 失败时的回退目标节点
    failed_card_ids: list[str] = Field(default_factory=list)
    failed_facts: list[FailedFact] = Field(default_factory=list)


class Gate1Result(BaseModel):
    """Gate 1 蓝图评估结果"""
    passed: bool = False
    dim_scores: list[Gate1DimScore] = Field(default_factory=list)
    overall_score: float = 0.0
    failed_dims: list[str] = Field(default_factory=list)
    voting_enabled: bool = False     # v3.1.x 默认 False


class Gate2Issue(BaseModel):
    """Gate 2 单项检查结果"""
    check_name: str                  # "overflow" | "font_size" | "ocr_typo" | "vision_score" | "fact_drift"
    passed: bool = True
    detail: str = ""
    issue_type: str = ""             # "render_only" | "blueprint_level" | "fact_drift"


class Gate2Result(BaseModel):
    """Gate 2 渲染评估结果"""
    passed: bool = False
    issues: list[Gate2Issue] = Field(default_factory=list)
    vision_score: float = 0.0
    ocr_skipped: bool = False        # OCR 未安装时为 True


class RetryBudgetState(BaseModel):
    """重试预算计数器（PRD 4.11.4–4.11.7）"""
    render_only: int = 0             # max 3
    blueprint_level: int = 0        # max 2
    fact_drift: int = 0             # max 1

    _LIMITS: dict = {"render_only": 3, "blueprint_level": 2, "fact_drift": 1}

    def can_retry(self, issue_type: str) -> bool:
        try:
            from infra.config import DEMO_SAFE_MODE, DEMO_RETRY_LIMITS
            limits = DEMO_RETRY_LIMITS if DEMO_SAFE_MODE else {"render_only": 3, "blueprint_level": 2, "fact_drift": 1}
        except ImportError:
            limits = {"render_only": 3, "blueprint_level": 2, "fact_drift": 1}
        limit = limits.get(issue_type, 0)
        current = getattr(self, issue_type, 0)
        return current < limit

    def incremented(self, issue_type: str) -> "RetryBudgetState":
        """返回增量后的新实例（不可变更新）。"""
        current = getattr(self, issue_type, 0)
        return self.model_copy(update={issue_type: current + 1})

    def snapshot(self) -> str:
        try:
            from infra.config import DEMO_SAFE_MODE, DEMO_RETRY_LIMITS
            limits = DEMO_RETRY_LIMITS if DEMO_SAFE_MODE else {"render_only": 3, "blueprint_level": 2, "fact_drift": 1}
        except ImportError:
            limits = {"render_only": 3, "blueprint_level": 2, "fact_drift": 1}
        return (f"render_only={self.render_only}/{limits['render_only']}, "
                f"blueprint_level={self.blueprint_level}/{limits['blueprint_level']}, "
                f"fact_drift={self.fact_drift}/{limits['fact_drift']}")
