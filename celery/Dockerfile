FROM python:3.6

# Silence Celery complaining about running as root user
ENV C_FORCE_ROOT true

RUN apt-get update
RUN apt-get install -y wait-for-it

COPY Pipfile /celery/Pipfile
COPY Pipfile.lock /celery/Pipfile.lock

WORKDIR /celery

RUN pip install pipenv
RUN pipenv update
RUN pipenv install --system --deploy --ignore-pipfile

COPY ./tasks.py /celery/tasks.py
COPY ./cub-attendance.json /celery/cub-attendance.json

# RUN touch celerybeat-schedule

ENTRYPOINT wait-for-it pg:5432 -- celery -A tasks worker -B --loglevel=${LOG_LEVEL} --concurrency=20 -s ./celerybeat-schedule
