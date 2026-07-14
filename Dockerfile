FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
ENV SD3_DB=/data/starrydata3.db
VOLUME /data
EXPOSE 8300
# gunicorn with uvicorn workers = production ASGI server
CMD ["gunicorn", "starrydata3.app:app", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8300", "--workers", "2"]
