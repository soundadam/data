# The data service. glibc (slim), not alpine: duckdb and pyarrow ship
# manylinux wheels only. requirements.txt is uv.lock exported with hashes
# (`make requirements`); the package itself runs from src/ on PYTHONPATH,
# so nothing is built here from an unpinned backend.
FROM docker.io/library/python:3.13.16-slim-trixie@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOME=/tmp
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: -r /app/requirements.txt
COPY src /app/src
USER 65534:65534
EXPOSE 8080 8081 8082
CMD ["python", "-m", "dataservice.app"]
