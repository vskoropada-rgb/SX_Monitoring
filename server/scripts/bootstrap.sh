#!/bin/bash
# bootstrap.sh — встановлення на свіжий Ubuntu 22.04
set -e

echo "=== SX Monitor Server Bootstrap ==="

# Docker
if ! command -v docker &>/dev/null; then
    echo "[1/4] Встановлення Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
else
    echo "[1/4] Docker вже встановлений"
fi

# Docker Compose v2
if ! docker compose version &>/dev/null 2>&1; then
    echo "[2/4] Встановлення Docker Compose..."
    apt-get install -y docker-compose-v2
else
    echo "[2/4] Docker Compose вже встановлений"
fi

# Клонування репо
if [ ! -d "sx-monitor-server" ]; then
    echo "[3/4] Клонування репозиторію..."
    git clone https://github.com/vskoropada-rgb/sx-monitor-server.git
    cd sx-monitor-server
else
    echo "[3/4] Репозиторій вже існує"
    cd sx-monitor-server
    git pull
fi

# .env
if [ ! -f ".env" ]; then
    echo "[4/4] Створення .env..."
    cp .env.example .env
    echo ""
    echo "УВАГА: Відредагуйте .env перед запуском!"
    echo "  nano .env"
    echo ""
    echo "Після налаштування запустіть:"
    echo "  docker compose up -d"
else
    echo "[4/4] .env вже існує"
    echo ""
    echo "Запуск сервісів..."
    docker compose up -d
    echo ""
    echo "=== Готово ==="
    docker compose ps
fi
