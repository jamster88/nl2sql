FROM python:3.12-slim

WORKDIR /app

COPY agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/nl2sql_agent/ ./nl2sql_agent/

ENTRYPOINT ["python", "-m", "nl2sql_agent"]
