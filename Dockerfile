FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8080

WORKDIR /app

# Install system dependencies if necessary
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Expose the port Cloud Run expects
EXPOSE 8080

# The data directory should be mounted as a volume in Cloud Run
# so the SQLite database persists across restarts.
RUN mkdir -p /app/data

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
