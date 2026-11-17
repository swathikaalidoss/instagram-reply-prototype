FROM node:22-alpine AS dashboard
WORKDIR /dashboard
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY app.py auth.py ./
COPY --from=dashboard /dashboard/dist ./static
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
