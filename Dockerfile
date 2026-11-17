FROM python:3.12-slim
WORKDIR /app
COPY app.py .
COPY static ./static
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
USER app
ENV PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
