FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 9000
CMD ["gunicorn", "--bind", "0.0.0.0:9000", "--workers", "1", "--threads", "4", "main:app"]
