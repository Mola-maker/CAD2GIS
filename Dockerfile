FROM python:3.12-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 \
    CAD2GIS_CACHE_DIR=/opt/cad2gis-cache
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN pip install '.[agent]' && cad2gis runtime install

FROM python:3.12-slim-bookworm AS runtime
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="CAD2GIS" \
      org.opencontainers.image.source="https://github.com/Mola-maker/CAD2GIS" \
      org.opencontainers.image.revision=$VCS_REF
ENV PATH="/opt/venv/bin:$PATH" \
    CAD2GIS_CACHE_DIR=/opt/cad2gis-cache \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 libgomp1 && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 cad2gis \
    && mkdir /data && chown cad2gis:cad2gis /data
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/cad2gis-cache /opt/cad2gis-cache
USER cad2gis
WORKDIR /data
RUN cad2gis doctor --deep --strict --profile full --json
EXPOSE 8765
ENTRYPOINT ["cad2gis"]
CMD ["doctor", "--deep", "--strict", "--profile", "full", "--json"]
