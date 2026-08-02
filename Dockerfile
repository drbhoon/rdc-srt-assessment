FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# psycopg2-binary ships wheels, so no build toolchain is needed. Only the
# runtime libs for reportlab's font handling are pulled in.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ROOT_PATH is the mount prefix on the HR platform ("/srt"). It is read at
# import time in main.py and is empty everywhere else, so the image is
# identical for root-mounted deployments.
ENV PORT=8000

EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
