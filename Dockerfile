# The API and the collector, in one image.
#
# They run as two containers from it (see docker-compose.yml) because they
# fail differently: the collector losing network for an hour is survivable and
# self-heals on reconnect, while the API going down means the phone shows
# nothing. Restarting one must not restart the other.
FROM python:3.12-slim

# libgomp1 is not optional: the frozen judges are LightGBM models, and
# importing lightgbm without OpenMP fails at load time with an error that
# reads like a missing Python package rather than a missing C library.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements first so a code change does not re-resolve the dependency tree
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# data_cache and output are bind-mounted in compose. Declaring them keeps a
# bare `docker run` from writing bars into the container's writable layer and
# losing them on the next image pull.
VOLUME ["/app/data_cache", "/app/output"]

ENV PYTHONUNBUFFERED=1
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8787/api/health || exit 1

CMD ["python3", "serve.py", "--host", "0.0.0.0", "--port", "8787"]
