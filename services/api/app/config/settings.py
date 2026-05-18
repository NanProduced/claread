from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    app_name: str = "Claread透读"
    app_env: str = "development"
    log_level: str = "INFO"
    default_model_profile: str = ""
    annotation_model_profile: str = ""
    dict_ai_model_profile: str = ""
    reader_ask_model_profile: str = ""
    daily_annotation_model_profile: str = ""
    daily_analysis_model_profile: str = ""
    daily_review_model_profile: str = ""
    model_profiles_json: str = ""
    model_presets_json: str = ""
    langsmith_enabled: bool = False
    langsmith_tracing: bool = True
    langsmith_project: str = "claread-dev"
    langsmith_api_key: str = ""
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_workspace_id: str = ""

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
