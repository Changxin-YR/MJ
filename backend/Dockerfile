FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN pip install --no-cache-dir -e '.[test]'
COPY . .
EXPOSE 8000
