FROM python:3.13-slim

# system libs for pyodbc + Postgres ODBC driver
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        unixodbc-dev odbc-postgresql && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY meme_bot.py meme_fetcher_4.py ./

# --- runtime environment ---------------
ENV PG_HOST=localhost \
    PG_PORT=my_port \
    PG_DB=my_database \
    PG_UID=my_user \
    PG_PWD=my_pass \
    SSH_HOST=                            \
    TELEGRAM_TOKEN=REPLACE_WITH_TOKEN

CMD ["python", "meme_bot.py"]
