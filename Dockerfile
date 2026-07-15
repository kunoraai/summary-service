FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/nonexistent

RUN groupadd --gid 10001 summary \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent summary \
    && mkdir -p /data \
    && chown 10001:10001 /data
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["summary-service"]
CMD ["api"]
