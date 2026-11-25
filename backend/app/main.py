"""FastAPI 入口：挂载 API、启动时自动播种、托管前端构建产物。"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import Base, SessionLocal, engine
from .models import User
from .routers import auth, cases, graph, hitrate, identity, patterns, snapshots, sources, tools

app = FastAPI(title=settings.APP_NAME, version="1.0.0",
              description="来源账 / 身份解析 / 受限图扩展 / 模式求值 / 反证编排 / "
                          "案件投影 / 快照复演 / 冻结排序命中率评估")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=False,
    allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, sources.router, identity.router, graph.router,
          patterns.router, cases.router, snapshots.router, tools.router,
          hitrate.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"ok": True, "app": settings.APP_NAME}


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            from .seed import run_seed
            run_seed(reset=False)
    finally:
        db.close()


# 单镜像部署：若前端已构建，则托管 dist（hash 路由，无需 SPA history fallback）
if os.path.isdir(settings.STATIC_DIR):
    assets = os.path.join(settings.STATIC_DIR, "assets")
    if os.path.isdir(assets):
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(settings.STATIC_DIR, "index.html"))
