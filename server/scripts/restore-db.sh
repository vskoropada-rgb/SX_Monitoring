#!/bin/bash
# Відновлення БД для міграції між провайдерами
# Використання: ./restore-db.sh backup_20240101_120000.sql
DUMP_FILE="$1"
if [ -z "$DUMP_FILE" ] || [ ! -f "$DUMP_FILE" ]; then
    echo "Використання: $0 <dump_file.sql>"
    exit 1
fi
echo "Відновлення з $DUMP_FILE..."
cat "$DUMP_FILE" | docker compose exec -T postgres psql -U monitor sx_monitor
echo "Готово"
