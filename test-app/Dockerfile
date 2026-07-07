FROM python:3.8-slim

WORKDIR /app

COPY . /app/

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install gunicorn

EXPOSE 8081

CMD ["gunicorn", "-k", "gevent", "-b", "0.0.0.0:8081", "main:app"]
