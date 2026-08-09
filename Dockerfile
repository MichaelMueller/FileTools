# MMO FileTools — web mode container image.
#
# Desktop mode (pywebview) is intentionally NOT supported here: it needs a
# native window and Windows/EdgeChromium. Only ``mmo_file_tools.main:app``
# (web mode) runs in this image.

FROM python:3.12-slim

# Build args so the runtime user can match the owner of the mounted data
# directory on the host (bind mounts keep host uid/gid).
ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# All runtime dependencies ship manylinux wheels (pypdfium2, Pillow, xxhash,
# greenlet, …), so no compiler is needed. pywebview is a hard dependency of
# the project but is imported lazily — web mode never touches it, so no
# GTK/WebKit packages are required.
COPY pyproject.toml ./
COPY mmo_file_tools ./mmo_file_tools
RUN pip install --no-cache-dir . \
    && groupadd -g "${APP_GID}" app \
    && useradd -u "${APP_UID}" -g "${APP_GID}" -m -d /home/app app \
    && mkdir -p /data /home/app/.local/share \
    && chown -R app:app /data /home/app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", \
         "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"]

# --proxy-headers/--forwarded-allow-ips let uvicorn honour X-Forwarded-* from
# the oauth2-proxy sidecar so redirects and logged client IPs stay correct.
CMD ["python", "-m", "uvicorn", "mmo_file_tools.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
