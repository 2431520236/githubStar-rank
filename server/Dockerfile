# 基础镜像可用 --build-arg 覆盖为国内加速地址:
#   docker compose build --build-arg BASE_IMAGE=docker.1ms.run/library/python:3.12-slim
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_INDEX_URL=${PIP_INDEX}

WORKDIR /app

# 不用 apt(国内访问 deb.debian.org 极慢):时区数据走 pip 包 tzdata
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir tzdata

COPY . .

EXPOSE 9951
CMD ["python3", "run.py"]
