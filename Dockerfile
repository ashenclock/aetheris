FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/aetheris

COPY pyproject.toml poetry.lock README.md LICENSE ./
COPY nexus ./nexus
COPY evals ./evals

RUN pip install --no-cache-dir . \
    && useradd --create-home --shell /usr/sbin/nologin aetheris

USER aetheris
WORKDIR /workspace

ENTRYPOINT ["aetheris"]
CMD ["--help"]
