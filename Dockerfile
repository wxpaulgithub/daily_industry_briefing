FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FETCH_NEWS_ON_STARTUP=true \
    OPPORTUNITY_RUNTIME_DIR=/runtime

# 构建参数：可按需覆盖
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_EXTRA_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/

# 下载加速配置
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}

# 安装依赖（利用 Docker 层缓存，依赖不变则不重新安装）
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir --default-timeout=300 --retries 20 \
       setuptools wheel \
    && pip install --no-cache-dir --default-timeout=300 --retries 20 --no-build-isolation \
       -r requirements.txt

# 复制项目文件
COPY app.py config.py fetch.py ./
COPY services/ services/
COPY templates/ templates/
COPY static/ static/
COPY config_data/ config_data/
COPY scripts/check_opportunity_config.py scripts/test_opportunity_ai_live.py ./scripts/

# 创建输出目录
RUN mkdir -p output runtime /runtime

# 时区设为上海（确保定时任务和日期显示正确）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

EXPOSE 8088

HEALTHCHECK --interval=10s --timeout=8s --start-period=30s --retries=12 \
    CMD python -c "import urllib.request; [urllib.request.urlopen('http://127.0.0.1:8088' + path, timeout=3).close() for path in ('/api/status', '/api/opportunities/status')]"

# 先启动 Web；启动资讯采集在 lifespan 中异步执行，不阻塞健康检查。
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8088"]
