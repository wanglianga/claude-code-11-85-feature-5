"""应用配置：环境变量优先，本地开发默认指向预装 PostgreSQL。"""
import os


class Settings:
    APP_NAME = "医保风险线索关系分析平台"
    API_PREFIX = "/api"
    # 容器内 compose 会用 DATABASE_URL 覆盖（db 服务）
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://ccdev:ccdev@127.0.0.1:5432/riskgraph",
    )
    # 初始快照/分析截止日（虚构网络的业务截止日，保证 180 天窗确定可复演）
    DEFAULT_CUTOFF = os.getenv("DEFAULT_CUTOFF", "2026-03-31")
    TOKEN_TTL_HOURS = int(os.getenv("TOKEN_TTL_HOURS", "12"))
    # 前端构建产物（单镜像部署时由 FastAPI 托管）
    STATIC_DIR = os.getenv("STATIC_DIR", "/workspace/85/frontend/dist")


settings = Settings()
