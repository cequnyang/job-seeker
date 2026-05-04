FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY config ./config
COPY samples ./samples
COPY run ./run
COPY scripts ./scripts
COPY README.md ./
COPY .env.example ./

RUN chmod +x ./run/*.sh

ENTRYPOINT ["./run/run_job_seeker.sh"]
