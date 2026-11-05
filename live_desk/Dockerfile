FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LIVE_HOST=0.0.0.0 LIVE_PORT=8010
WORKDIR /app
COPY requirements-bridge.txt requirements-live-test.txt requirements-live.txt ./
RUN python -m pip install --no-cache-dir -r requirements-live.txt
COPY bridge/ ./bridge/
COPY scripts/ ./scripts/
COPY live_desk/ ./live_desk/
RUN useradd -r -u 10011 -m liveuser && mkdir -p /data && chown -R liveuser:liveuser /data
USER liveuser
EXPOSE 8010
CMD ["python","-m","live_desk.app"]
