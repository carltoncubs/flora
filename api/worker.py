import os
from celery import Celery

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")

celery = Celery("tasks", broker=CELERY_BROKER_URL, backend="rpc://")
