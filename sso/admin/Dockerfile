FROM python:3.13-slim
RUN pip install --no-cache-dir argon2-cffi==23.1.0 PyYAML==6.0.2
WORKDIR /app
COPY app.py words.txt ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "app.py"]
