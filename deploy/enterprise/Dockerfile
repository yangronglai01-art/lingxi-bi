FROM node:22-bookworm-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d AS portal
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production QUERY_ENGINE=starrocks
WORKDIR /app
COPY requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt && useradd --uid 10001 --create-home analytics
COPY src ./src
COPY --from=portal /build/dist ./web/dist
USER 10001:10001
EXPOSE 8080
CMD ["python","-m","uvicorn","src.enterprise.api:create_app","--factory","--host","0.0.0.0","--port","8080","--no-access-log","--limit-concurrency","64"]
