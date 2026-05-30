ARG BASE_IMAGE=md-convert-base:latest
FROM ${BASE_IMAGE}

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV TMPDIR=/app/tmp

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
