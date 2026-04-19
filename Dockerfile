FROM python:3.13-slim

WORKDIR /app

# 替换 Debian 12 (Bookworm) 软件源为阿里云镜像，加速 Playwright 安装系统依赖的操作
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's/security.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# 构建参数：可按需覆盖（彻底去除境外 pip 源）
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_EXTRA_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple/
ARG PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright

# 下载加速配置
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL}
ENV PLAYWRIGHT_DOWNLOAD_HOST=${PLAYWRIGHT_DOWNLOAD_HOST}

# 安装依赖（利用 Docker 层缓存，依赖不变则不重新安装）
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir --default-timeout=300 --retries 20 \
       --index-url ${PIP_INDEX_URL} --extra-index-url ${PIP_EXTRA_INDEX_URL} \
       setuptools wheel \
    && pip install --no-cache-dir --default-timeout=300 --retries 20 --no-build-isolation \
       --index-url ${PIP_INDEX_URL} --extra-index-url ${PIP_EXTRA_INDEX_URL} \
       -r requirements.txt \
    && python -m playwright install --with-deps chromium

# 复制项目文件
COPY app.py config.py fetch.py ./
COPY services/ services/
COPY templates/ templates/
COPY static/ static/
COPY config_data/ config_data/

# 创建输出目录
RUN mkdir -p output

# 时区设为上海（确保定时任务和日期显示正确）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

EXPOSE 8088

# 启动时先采集一次资讯，再启动 Web 服务
CMD ["sh", "-c", "python fetch.py && python -m uvicorn app:app --host 0.0.0.0 --port 8088"]
