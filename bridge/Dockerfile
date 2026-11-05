FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SIGNAL_BRIDGE_HOST=0.0.0.0 SIGNAL_BRIDGE_PORT=8000
WORKDIR /app
COPY requirements-bridge.txt /app/
RUN python -m pip install --no-cache-dir -r requirements-bridge.txt
COPY bridge/ /app/bridge/
COPY scripts/ /app/scripts/
RUN mkdir -p /data && useradd -m bridgeuser && chown -R bridgeuser:bridgeuser /app /data
USER bridgeuser
EXPOSE 8000
CMD ["python", "-m", "bridge.app"]
