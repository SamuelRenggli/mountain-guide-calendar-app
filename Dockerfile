FROM python:3.11-slim

WORKDIR /app

# System-Abhängigkeiten für MySQL-Treiber installieren
RUN apt-get update && apt-get install -y gcc default-libmysqlclient-dev pkg-config && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
