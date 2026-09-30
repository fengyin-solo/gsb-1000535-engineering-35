# ---- 构建阶段：产出带批次构建清单的静态产物 ----
FROM node:20-alpine AS build
WORKDIR /srv/web
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
ARG APP_VERSION=1.0.0
ENV APP_VERSION=${APP_VERSION}
RUN npm run build

# ---- 运行阶段：nginx 托管静态产物，/api 反代到后端 ----
FROM nginx:1.27-alpine
COPY --from=build /srv/web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
