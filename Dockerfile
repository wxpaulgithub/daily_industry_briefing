FROM python:3.13-slim

WORKDIR /app

# 安装依赖（利用 Docker 层缓存，依赖不变则不重新安装）
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install --no-cache-dir --default-timeout=120 --retries 10 -r requirements.txt

# 复制项目文件
COPY app.py config.py fetch.py ./
COPY services/ services/
COPY templates/ templates/
COPY static/ static/

# 创建输出目录
RUN mkdir -p output

# 时区设为上海（确保定时任务和日期显示正确）
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

EXPOSE 8088

# 启动时先采集一次资讯，再启动 Web 服务
CMD ["sh", "-c", "python fetch.py && python -m uvicorn app:app --host 0.0.0.0 --port 8088"]