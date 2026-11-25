# ---------- 前端构建 ----------
FROM node:22-alpine AS frontend
WORKDIR /fe
RUN npm config set registry https://registry.npmmirror.com
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ---------- 后端运行时（同时托管前端 dist） ----------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/frontend/dist \
    TZ=Asia/Shanghai
WORKDIR /app

# 清华 pip 源（如在境外网络构建，可去掉 -i 参数）
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

COPY backend/ ./
COPY --from=frontend /fe/dist ./frontend/dist

EXPOSE 8000
# 容器启动时会自动 create_all 并在空库播种虚构验收网络
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
