FROM python:3.6

# Silence Celery complaining about running as root user
ENV C_FORCE_ROOT true

RUN apt-get update
RUN apt-get install -y wait-for-it

COPY Pipfile /api/Pipfile
COPY Pipfile.lock /api/Pipfile.lock

WORKDIR /api

RUN pip install pipenv
RUN pipenv update
RUN pipenv install --system --deploy --ignore-pipfile

COPY worker.py /api/worker.py
COPY models.py /api/models.py
COPY api.py /api/api.py

ENTRYPOINT wait-for-it pg:5432 -- gunicorn --bind 0.0.0.0:8000 --workers 3 --log-level ${LOG_LEVEL} api:app
