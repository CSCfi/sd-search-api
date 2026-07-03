#=======================
FROM ghcr.io/astral-sh/uv:python3.14-trixie
#=======================

LABEL maintainer="CSC Developers"
LABEL org.label-schema.schema-version="1.0"
LABEL org.label-schema.vcs-url="https://github.com/CSCfi/sd-search-api"

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY README.md .
COPY search_api ./search_api

RUN uv sync --frozen && \
    chgrp -R 0 /app && \
    chmod -R g=u /app

ENV HOME=/app \
    UV_CACHE_DIR=/app/.cache/uv

ENTRYPOINT ["uv", "run", "sd_search_api"]
