# Single image: builds the UI, then serves UI + API from FastAPI on port 8000.
# NOT VERIFIED in the development environment (no Docker available there). See docs/INSTALLATION.md.
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
RUN useradd --create-home --uid 10001 aegis
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=ui /ui/dist frontend/dist
RUN mkdir -p data && chown -R aegis:aegis /app
USER aegis
ENV AEGIS_DATA_DIR=/app/data
WORKDIR /app/backend
EXPOSE 8000
CMD ["sh", "-c", "python -m app.cli init && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
