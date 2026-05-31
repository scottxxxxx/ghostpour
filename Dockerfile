FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY config/ config/

RUN mkdir -p /app/data

# Bake the git SHA into the image so /health (and the admin dashboard
# header badge) can report which commit is actually serving traffic.
# Passed in by .github/workflows/deploy.yml via --build-arg GIT_SHA.
# Defaults to "unknown" for local builds where the arg isn't supplied.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
