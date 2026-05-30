#!/bin/bash
# Бекап PostgreSQL → файл з датою
BACKUP_DIR="${1:-./backups}"
mkdir -p "$BACKUP_DIR"
FILENAME="$BACKUP_DIR/sx_monitor_$(date +%Y%m%d_%H%M%S).sql"
docker compose exec -T postgres pg_dump -U monitor sx_monitor > "$FILENAME"
echo "Збережено: $FILENAME ($(du -sh "$FILENAME" | cut -f1))"
