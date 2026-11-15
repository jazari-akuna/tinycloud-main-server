FROM python:3.13-slim
WORKDIR /app
COPY app.py ./
ENV PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "app.py"]
