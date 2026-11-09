#!/bin/sh
set -eu

alembic -c alembic.ini upgrade head
exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --proxy-headers

