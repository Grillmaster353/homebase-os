# ARM64 Apple TV dependency miniaudio may compile from source. Build wheels in
# a separate stage so the running image contains no compiler toolchain.
FROM python:3.12-slim AS wheels
WORKDIR /build
COPY requirements.txt requirements-tv.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -r requirements-tv.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /opt/home-command
COPY requirements.txt requirements-tv.txt ./
COPY --from=wheels /wheels /wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt -r requirements-tv.txt \
    && rm -rf /wheels \
    && groupadd --gid 10001 homecommand \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin homecommand \
    && mkdir -p /data /workspace \
    && chown homecommand:homecommand /data /workspace
COPY --chown=homecommand:homecommand app ./app
COPY --chown=homecommand:homecommand web ./web
USER 10001:10001
VOLUME ["/data", "/workspace"]
EXPOSE 8420
ENTRYPOINT ["python", "-m", "app.server"]
CMD ["--host", "127.0.0.1", "--port", "8420", "--data-dir", "/data"]
