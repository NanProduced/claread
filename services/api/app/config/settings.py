from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.contracts.article_rag_contract import ARTICLE_RAG_EMBEDDING_CONTRACT


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


@lru_cache(maxsize=1)
def _load_local_env_values() -> dict[str, str]:
    env_path = _get_project_root() / ".env"
    if not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


class Settings(BaseSettings):
    app_name: str = "Claread透读"
    app_env: str = "development"
    log_level: str = "INFO"
    default_model_profile: str = ""
    annotation_model_profile: str = ""
    dict_ai_model_profile: str = ""
    ask_claread_profile: str = ""
    reader_translation_model_profile: str = ""
    reader_vocabulary_model_profile: str = ""
    reader_grammar_bundle_model_profile: str = ""
    reader_title_model_profile: str = ""
    # T5.8a: semantic outline registration only — default empty/disabled.
    # Presence of these fields is NOT a live kill-switch wiring (T5.8b/d).
    reader_semantic_outline_model_profile: str = ""
    semantic_outline_generation_enabled: bool = False
    reader_worker_scan_interval_seconds: int = 5
    reader_worker_batch_size: int = 10
    # T1 acceptance: aligned with pipeline_runner.DEFAULT_PIPELINE_MAX_TICKS /
    # DEFAULT_PIPELINE_MAX_JOBS so the CLI, worker loop, and smoke harness all
    # share the same medium-sample budget. See pipeline_runner.py for the math.
    reader_worker_max_ticks: int = 96
    reader_worker_max_jobs: int = 48
    reader_worker_lease_duration_seconds: int = 120
    reader_worker_lease_owner_prefix: str = "reader-enhancement-worker"
    # Reading Record Ask agentic lane (default OFF — dual-path coexistence).
    # When true, validated Reading Record Ask message streams use the
    # independent reader_record_ask agent runtime instead of ask_runtime.
    # Failures do not fall back to the legacy agent.
    reader_record_ask_agentic_enabled: bool = False
    # Round 16: ``reader_ask_planner_model_profile`` has been removed.
    # The live agent-loop-first path no longer resolves a planner LLM.
    reader_ask_replan_model_profile: str = ""
    daily_annotation_model_profile: str = ""
    daily_analysis_model_profile: str = ""
    daily_review_model_profile: str = ""
    rag_embedding_model_profile: str = ""
    rag_rerank_model_profile: str = ""
    model_profiles_json: str = ""
    model_presets_json: str = ""
    reader_ask_model_options_json: str = ""
    langsmith_enabled: bool = False
    langsmith_tracing: bool = True
    langsmith_project: str = "claread-dev"
    langsmith_api_key: str = ""
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str = ""
    # PydanticAI 1.x ships ``Agent.instrument_all()`` which emits OpenTelemetry
    # spans for every ``agent.run``. LangSmith's official OTEL integration
    # (``langsmith.integrations.otel.configure``) wires those spans to the
    # LangSmith project. Disabled by default for tests; production should set
    # ``LANGSMITH_OTEL_ENABLED=true``.
    langsmith_otel_enabled: bool = False

    # 数据库
    database_url: str = "postgresql://claread:claread_dev@127.0.0.1:5432/claread"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_max_inactive_connection_lifetime: int = 3600

    # Redis（可选，第二阶段增强）
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_enabled: bool = False

    # 微信认证
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    auth_session_expiry_days: int = 30

    # 手机号认证
    # development 默认 mock；生产接入阿里云云通信号码认证服务 Dypnsapi。
    phone_auth_provider: str = "mock"
    phone_mock_verification_code: str = "888888"
    aliyun_dypnsapi_access_key_id: str = ""
    aliyun_dypnsapi_access_key_secret: str = ""
    aliyun_dypnsapi_endpoint: str = "dypnsapi.aliyuncs.com"
    aliyun_dypnsapi_region_id: str = "cn-hangzhou"
    aliyun_dypnsapi_sign_name: str = ""
    aliyun_dypnsapi_login_template_code: str = "100001"
    aliyun_dypnsapi_code_ttl_minutes: int = 5
    aliyun_dypnsapi_code_length: int = 6
    aliyun_dypnsapi_send_interval_seconds: int = 60

    # 每日精读
    guardian_api_key: str = ""
    daily_reader_admin_openid: str = ""
    daily_reader_admin_api_key: str = ""
    eval_admin_api_key: str = ""
    server_base_url: str = "http://127.0.0.1:8000"

    # Grammar RAG（默认关闭，Readiness Gate 阶段仅做骨架接入）
    grammar_rag_enabled: bool = False

    # Zilliz 向量数据库（Grammar RAG 依赖）
    zilliz_uri: str = ""
    zilliz_token: str = ""
    zilliz_collection_grammar_note: str = "grammar_note_examples"
    zilliz_collection_sentence_analysis: str = "sentence_analysis_examples"

    # 百炼 Embedding（Grammar RAG 依赖）
    bailian_api_key: str = ""
    bailian_embedding_model: str = "text-embedding-v4"
    bailian_embedding_dimension: int = 1024

    # 百炼 Rerank（Grammar RAG 依赖）
    bailian_rerank_model: str = "qwen3-rerank"

    # RAG 运行参数
    grammar_rag_ann_topk: int = 8
    grammar_rag_rerank_topn: int = 5
    grammar_rag_confidence_threshold: float = 0.3

    # 内部 API Key（云函数调用等）
    internal_api_key: str = ""

    # Aliyun OSS（D6-I3Q）
    # 凭证从环境变量读取，不写入代码、测试或 API 响应。
    # dev 默认 bucket/endpoint 保持 claread-dev / oss-cn-shenzhen 兼容。
    # presign_enabled=False 时默认 fail closed（NullPresigner），客户端走 pending credentials 语义。
    aliyun_oss_access_key_id: str = ""
    aliyun_oss_access_key_secret: str = ""
    aliyun_oss_bucket: str = "claread-dev"
    aliyun_oss_endpoint: str = "https://oss-cn-shenzhen.aliyuncs.com"
    aliyun_oss_presign_enabled: bool = False
    aliyun_oss_presign_expires_seconds: int = 900

    # Reader artifact pipeline worker (D6-I3R)
    reader_artifact_worker_poll_interval_seconds: int = 5
    reader_artifact_worker_lease_owner_prefix: str = "reader-artifact-pipeline-worker"
    reader_artifact_worker_lease_duration_seconds: int = 120
    reader_artifact_worker_max_ticks: int = 100

    # Reader OCR provider (D6-I3T + D6-I3U)
    # 默认 disabled：本地 worker 可启动但 image/* job terminal fail closed
    # (ocr_provider_unconfigured)。启用后需要单独配置 DASHSCOPE_API_KEY 等
    # 凭证（不在 settings 默认值中写入密钥）。
    # confidence 阈值只产生 warnings，不直接 reject；后续 materialization
    # gate 会按 ocr_text 进入 candidate_document_required。
    reader_ocr_provider_enabled: bool = False
    reader_ocr_provider_name: str = "qwen"
    reader_ocr_min_text_confidence: float = 0.75
    reader_ocr_min_layout_confidence: float = 0.65
    # D6-I3U: real Qwen OCR adapter settings.
    # 模型名可配置，默认 qwen3.5-ocr；可通过 env/settings 覆盖。
    reader_ocr_qwen_model: str = "qwen3.5-ocr"
    # 单次 OCR 请求超时（秒）；超时映射为 retryable ocr_backend_transient。
    reader_ocr_request_timeout_seconds: int = 60

    # D6-I4D: Article RAG provider adapter foundation.
    # 默认 disabled + 无 zilliz 凭证 / 无 embedding provider 时，
    # factory 返回 Unconfigured* 包装（fail closed，不联网）。
    # README/Zilliz 仅作为 index replica；citation truth 永远回 Postgres。
    #
    # The default Zilliz collection + vector dim are sourced from the
    # frozen contract in ``app.contracts.article_rag_contract`` so there
    # is a single Python source of truth for the Article RAG vector-space
    # identity (no duplicate ``article_rag_chunks`` literal here).
    reader_article_rag_embedding_provider: str = ""
    reader_article_rag_embedding_model: str = ""
    reader_article_rag_vector_provider: str = ""
    reader_article_rag_zilliz_uri: str = ""
    reader_article_rag_zilliz_token: str = ""
    reader_article_rag_zilliz_collection: str = ARTICLE_RAG_EMBEDDING_CONTRACT.vector_collection
    reader_article_rag_vector_dim: int = ARTICLE_RAG_EMBEDDING_CONTRACT.document_embedding_dimension
    reader_article_rag_enabled: bool = False
    reader_article_rag_smoke: bool = False

    # D6-I4U: Article RAG index worker entry (poll/lease/max-ticks).
    # Mirrors reader_artifact_worker_* defaults; the entry script reads these
    # as CLI argument defaults. Missing DashScope/Zilliz config does NOT
    # prevent worker startup — providers fail closed on first job.
    reader_article_rag_worker_poll_interval_seconds: int = 5
    reader_article_rag_worker_lease_owner_prefix: str = "reader-article-rag-index-worker"
    reader_article_rag_worker_lease_duration_seconds: int = 120
    reader_article_rag_worker_max_ticks: int = 100

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def resolve_config_path(self, path: str) -> str:
        if not path:
            return path
        if os.path.isabs(path):
            return path
        if path.startswith("config/"):
            return str(_get_project_root() / path)
        return path

    def resolve_external_env_var(self, env_name: str, *, fallback: str = "") -> str:
        if not env_name:
            return fallback
        runtime_value = os.getenv(env_name)
        if runtime_value:
            return runtime_value
        return _load_local_env_values().get(env_name, fallback)

    def resolve_aliyun_oss_credentials(self) -> tuple[str, str]:
        """Resolve OSS AccessKey credentials as a pair.

        OSS may use dedicated ``ALIYUN_OSS_*`` credentials in production. For
        local development, fall back to the common Alibaba Cloud SDK variables
        so users do not have to duplicate secrets in ``.env``.

        Do not mix one OSS-specific value with one generic Alibaba Cloud value:
        if either OSS-specific credential is configured, both must be present.
        """
        if self.aliyun_oss_access_key_id or self.aliyun_oss_access_key_secret:
            return (
                self.aliyun_oss_access_key_id,
                self.aliyun_oss_access_key_secret,
            )
        return (
            self.resolve_external_env_var("ALIBABA_CLOUD_ACCESS_KEY_ID", fallback=""),
            self.resolve_external_env_var("ALIBABA_CLOUD_ACCESS_KEY_SECRET", fallback=""),
        )

    def resolve_aliyun_oss_access_key_id(self) -> str:
        """Resolve OSS AccessKey id."""
        access_key_id, _ = self.resolve_aliyun_oss_credentials()
        return access_key_id

    def resolve_aliyun_oss_access_key_secret(self) -> str:
        """Resolve OSS AccessKey secret without exposing it to API responses."""
        _, access_key_secret = self.resolve_aliyun_oss_credentials()
        return access_key_secret

    def resolve_reader_article_rag_zilliz_uri(self) -> str:
        """Resolve Article RAG Zilliz URI.

        Article RAG may use dedicated ``READER_ARTICLE_RAG_ZILLIZ_URI`` in
        deployment.  For local development, fall back to the existing
        few-shot/Grammar RAG ``ZILLIZ_URI`` so users do not duplicate the
        same secret-bearing endpoint.  The Article RAG collection remains
        independent via ``reader_article_rag_zilliz_collection``.
        """
        if self.reader_article_rag_zilliz_uri:
            return self.reader_article_rag_zilliz_uri
        return self.resolve_external_env_var("ZILLIZ_URI", fallback="")

    def resolve_reader_article_rag_zilliz_token(self) -> str:
        """Resolve Article RAG Zilliz token without exposing it in logs."""
        if self.reader_article_rag_zilliz_token:
            return self.reader_article_rag_zilliz_token
        return self.resolve_external_env_var("ZILLIZ_TOKEN", fallback="")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
