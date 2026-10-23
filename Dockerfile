FROM python:3.12-slim-bookworm

ARG APP_UID=1000
ARG APP_GID=1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/app/data HEADLESS=false CHROMIUM_NO_SANDBOX=true \
    CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium CHROMEDRIVER_PATH=/usr/bin/chromedriver
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver xvfb xauth tini fonts-liberation ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "${APP_GID}" monitor \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home monitor
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=monitor:monitor . .
RUN mkdir -p /app/data && chown monitor:monitor /app/data
USER monitor
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "xvfb-run", "-a", "-s", "-screen 0 1440x1000x24"]
CMD ["python", "main.py"]
