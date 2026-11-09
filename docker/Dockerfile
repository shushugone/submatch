# 国内网络用镜像源；海外可改成 python:3.12-slim
ARG BASE_IMAGE=docker.1ms.run/library/python:3.12-slim
FROM ${BASE_IMAGE}

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libarchive-tools tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY submatch_core.py submatch_web.py submatch_headless.py /app/

ENV PYTHONUNBUFFERED=1 \
    SUBMATCH_CONFIG_DIR=/config \
    TZ=Asia/Shanghai \
    WEB_PORT=8099

EXPOSE 8099
VOLUME ["/media"]
CMD ["python3", "/app/submatch_web.py"]
