FROM python:3.12-slim

# 业务时区依赖 zoneinfo：slim 镜像默认缺 tzdata，必须显式安装，
# 跨日补录按 BUSINESS_TIMEZONE 切日才可靠
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV APP_ENV=local \
    BUSINESS_TIMEZONE=Asia/Shanghai \
    DATA_DIR=/srv/data \
    AUTO_BOOTSTRAP=0

EXPOSE 8000

# 容器启动先跑“部署前探测 + 流水线”，成功后才启动服务；
# preflight 失败（缓存/队列不可用）entrypoint 以非零码退出，发布被阻断。
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
