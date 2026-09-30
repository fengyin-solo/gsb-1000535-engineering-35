# ---- 构建阶段：产物可重复（同样源码 -> 同样 dist） ----
FROM node:20-alpine AS build
WORKDIR /srv/web
COPY package.json ./
RUN npm install
COPY . .
ARG VITE_API_BASE=
ENV VITE_API_BASE=${VITE_API_BASE}
RUN npm run build

# ---- 部署阶段：nginx 托管静态产物，并把 /api 反代到后端 ----
FROM nginx:1.27-alpine AS runtime
COPY --from=build /srv/web/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
