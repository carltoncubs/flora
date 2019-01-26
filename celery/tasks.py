import os
from celery import Celery, states
from celery.utils.log import get_task_logger
from celery.exceptions import Ignore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from google.oauth2 import service_account
from googleapiclient.discovery import build

SERVICE_CREDS_FILE = os.getenv("SERVICE_CREDS_FILE")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

celery = Celery("tasks", broker=CELERY_BROKER_URL, backend="rpc://")
engine = create_engine(os.getenv("SQLALCHEMY_DATABASE_URI"))
DBSession = sessionmaker(bind=engine)
session = DBSession()
logger = get_task_logger(__name__)


@celery.task(name="tasks.record_attendance", bind=True)
def record_attendance(self, spreadsheet_id, sheet_name, **kwargs):
    """Record a cub's attendance in the google sheet and clear the
    attendance record out of the database."""
    query = session.query("user").filter_by(**kwargs)
    if not session.query(query.exists()).scalar():
        logger.error("No attendance records found for %s", kwargs["cub_name"])
        self.update_state(
            state=states.FAILURE,
            meta=f"No attendance records found for {kwargs['cub_name']}",
        )
        raise Ignore()

    record = query.first()
    client = make_sheets_client(SERVICE_CREDS_FILE)
    resp = append_row(
        client,
        spreadsheet_id,
        sheet_name,
        [
            kwargs["cub_name"],
            kwargs["cub_signature_in"],
            kwargs["parent_signature_in"],
            kwargs["parent_signature_out"],
            kwargs["time_in"],
            kwargs["date_in"],
            kwargs["time_out"],
            kwargs["date_out"],
        ],
    )
    if not resp.ok:
        logger.error("Failed to append record for {%s}", kwargs["cub_name"])
        self.update_state(
            state=states.FAILURE, meta=f"Failed to append record for {kwargs['cub_name']}"
        )
        raise Ignore()

    session.delete(record)
    session.commit()


def append_row(client, spreadsheet_id, sheet_name, row):
    result = (
        client.values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
            valueInputOption="RAW",
            body={"values": [row]},
        )
        .execute()
    )
    logger.debug(f"Append result: {result}")
    return ("Successfully appended row", True)


def make_sheets_client(service_creds_file):
    creds = service_account.Credentials.from_service_account_file(
        service_creds_file, scopes=["profile", "email", SHEETS_SCOPE]
    )
    creds.with_scopes([SHEETS_SCOPE])
    return build("sheets", "v4", credentials=creds).spreadsheets()
