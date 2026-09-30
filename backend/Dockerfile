FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    APP_ENV=prod \
    BUSINESS_TZ=Asia/Shanghai \
    PIPELINE_STATE_DIR=/srv/app/var/pipeline

WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 前端构建产物随镜像分发，部署闸门据此核对批次构建清单
# （docker-compose 用卷挂载 ../frontend/dist；独立构建镜像时由 CI 先构建再 COPY）
RUN mkdir -p /srv/frontend/dist
VOLUME ["/srv/app/var"]

RUN chmod +x entrypoint.sh
EXPOSE 8000
# 入口先跑流水线：未成功发布（含缓存/队列不可用）直接退出，不启动服务
ENTRYPOINT ["./entrypoint.sh"]
