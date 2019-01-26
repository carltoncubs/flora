FROM python:3.6

# Silence Celery complaining about running as root user
ENV C_FORCE_ROOT true

RUN apt-get update
RUN apt-get install -y wait-for-it

COPY Pipfile /celery/Pipfile
COPY Pipfile.lock /celery/Pipfile.lock

WORKDIR /celery

RUN pip install pipenv
RUN pipenv install --system --deploy --ignore-pipfile

COPY tasks.py /celery/tasks.py

ENTRYPOINT wait-for-it pg:5432 -- celery -A tasks worker --loglevel=debug --concurrency=20
