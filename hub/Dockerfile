FROM python:3.13-slim
RUN pip install --no-cache-dir PyYAML==6.0.2
WORKDIR /app
COPY app.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1
USER 1000:1000
CMD ["python", "app.py"]
