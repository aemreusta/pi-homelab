# MLflow tracking server (arm64) — PostgreSQL backend + MinIO/S3 artifact proxy.
# Built with uv (never pip). The upstream ghcr.io/mlflow/mlflow slim image ships
# without a Postgres driver or boto3; a Postgres backend and S3 artifacts need both, so we
# add them here (PRD-004 Faz-2; rationale in the mlflow role README).
# Operator directive: no version pinning — track latest. `build: always` pulls a
# fresh base each deploy. (3.12 line kept for psycopg/boto3 wheel coverage.)
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt
EXPOSE 5000
# The `mlflow server` invocation (backend/artifact URIs, flags) is supplied by
# docker-compose so the Ansible-templated config — not the image — owns it.
