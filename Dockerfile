FROM node:24-alpine AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY assets ./assets
COPY templates ./templates
COPY scripts/build-assets.mjs ./scripts/build-assets.mjs
RUN npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd --gid 10001 plyn && useradd --uid 10001 --gid plyn --create-home plyn
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=plyn:plyn . .
COPY --from=assets --chown=plyn:plyn /build/static/css ./static/css
COPY --from=assets --chown=plyn:plyn /build/static/js/htmx.min.js ./static/js/htmx.min.js
COPY --from=assets --chown=plyn:plyn /build/static/images ./static/images
RUN mkdir -p data/media data/mail staticfiles && chown -R plyn:plyn data staticfiles
USER plyn
RUN python manage.py collectstatic --noinput
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=3)" || exit 1
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]
