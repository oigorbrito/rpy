FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY requirements ./requirements
COPY app ./app
RUN python -m pip install pip==26.2.1 \
    && python -m pip install --constraint requirements/constraints.txt .

COPY data ./data
COPY sql ./sql
COPY scripts ./scripts

RUN useradd --create-home --uid 10001 rpy && chown -R rpy:rpy /app
USER rpy

EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
