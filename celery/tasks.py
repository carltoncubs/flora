import os
from celery import Celery, states
from celery.utils.log import get_task_logger
from celery.exceptions import Ignore
from google.oauth2 import service_account
from googleapiclient.discovery import build
from uritemplate import URITemplate
from collections import namedtuple
from sqlalchemy.ext.automap import automap_base
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

SERVICE_CREDS_FILE = os.getenv("SERVICE_CREDS_FILE")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL")
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SQLALCHEMY_DATBASE_URI = os.getenv("SQLALCHEMY_DATABASE_URI")

celery = Celery("tasks", broker=CELERY_BROKER_URL, backend="rpc://")
logger = get_task_logger(__name__)


SHEETS_APPEND_URL = URITemplate(
    "https://sheets.googleapis.com/v4/spreadsheets{/spreadsheetId}/values{/range_}:append"
)
SHEETS_GET_URL = URITemplate(
    "https://sheets.googleapis.com/v4/spreadsheets{/spreadsheetId}/values{/range_}"
)
SHEETS_UPDATE_URL = URITemplate(
    "https://sheets.googleapis.com/v4/spreadsheets{/spreadsheetId}/values{/range_}"
)

Base = automap_base()
engine = create_engine(SQLALCHEMY_DATBASE_URI)
Base.prepare(engine, reflect=True)
Name = Base.classes.name
Settings = Base.classes.settings
User = Base.classes.user

session = Session(engine)


def make_sheets_client(service_creds_file):
    creds = service_account.Credentials.from_service_account_file(
        service_creds_file, scopes=[SHEETS_SCOPE]
    )
    return build("sheets", "v4", credentials=creds).spreadsheets()


@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(
        5 * 60,
        update_name_autocomplete.s(),
        name="Update the autocomplete table for every user",
    )


@celery.task(name="tasks.update_name_autocomplete")
def update_name_autocomplete() -> None:
    logger.info("Updating autocomplete table")
    logger.debug("Building sheets client from %s", SERVICE_CREDS_FILE)
    sheets = make_sheets_client(SERVICE_CREDS_FILE)

    users = session.query(User).all()
    for user in users:
        settings = (
            sesson.query(Settings).join(models.User).filter_by(user_id=user.id).first()
        )
        sheet_name = settings.autocomplete_sheet
        result = (
            sheets.values()
            .get(spreadsheetId=settings.spreadsheet_id, range=f"{sheet_name}!A2:A")
            .execute()
        )

        names = []
        if result.get("values"):
            for name in result["values"]:
                names.append(name[0])
            session.query(Name).filter(user_id == user.id).delete()
            for name in names:
                session.add(Name(name=name, user_id=user.id))
            session.commit()
        else:
            logger.debug(f"No 'values' field in {result}")


@celery.task(name="tasks.add_sign_in", bind=True)
def add_sign_in(
    self,
    cub_name: str = "",
    cub_sig: str = "",
    parent_sig: str = "",
    time: str = "",
    date: str = "",
    spreadsheet_id: str = "",
    sheet_name: str = "",
) -> None:
    """Add a sign in record to the sheet. This function appends a
    row to the table containing the cub's name and signature, the parent's
    name and signature and a timestamp.

    """
    logger.debug("Building sheets client from %s", SERVICE_CREDS_FILE)
    sheets = make_sheets_client(SERVICE_CREDS_FILE)
    append = append_row(
        sheets,
        spreadsheet_id,
        sheet_name,
        [cub_name, cub_sig, parent_sig, "", time, "", date],
    )
    if not append.ok:
        logger.info("Could not add sign in to sheet %s", spreadsheet_id)
        self.update_state(state=states.FAILURE, meta=append.message)
        raise Ignore()


@celery.task(name="tasks.add_sign_out", bind=True)
def add_sign_out(
    self,
    cub_name: str = "",
    parent_sig: str = "",
    time: str = "",
    date: str = "",
    spreadsheet_id: str = "",
    sheet_name: str = "",
) -> None:
    """Record a sign out on the Google Sheet. This function is slightly
    more complex than :func:`add_sign_in` because it updates the row with
    the sign in corresponding to this sign out. To do this it looks for a
    row with the same cub_name and a timestamp for the same day. If it
    cannot find the appropriate record then it appends a new record to the
    table like :func:`add_sign_in`.

    """
    logger.debug("Building sheets client from %s", SERVICE_CREDS_FILE)
    sheets = make_sheets_client(SERVICE_CREDS_FILE)
    result = (
        sheets.values().get(spreadsheetId=spreadsheet_id, range=sheet_name).execute()
    )

    logger.debug(f"Get sheet result: {result}")

    updated_row_index = -1
    updated_row = ()
    for i, row in enumerate(result["values"]):
        (
            row_cub_name,
            row_cub_sig,
            row_parent_sign_in,
            _,
            row_time_in,
            _,
            row_date,
        ) = row
        if row_cub_name == cub_name and row_date == date:
            updated_row_index = i
            updated_row = (
                cub_name,
                row_cub_sig,
                row_parent_sign_in,
                parent_sig,
                row_time_in,
                time,
                date,
            )

    if updated_row_index == -1:
        logger.info("Unable to find corresponding sign in record, appending row")
        append = append_row(
            sheets,
            spreadsheet_id,
            sheet_name,
            [cub_name, "", "", parent_sig, "", time, date],
        )
        if not append.ok:
            logger.info("Could not add sign in to sheet %s", spreadsheet_id)
            self.update_state(state=states.FAILURE, meta=append.message)
            raise Ignore()
        return

    logger.info(f"Found corresponding sign in record, updating row {updated_row}")
    result = (
        sheets.values()
        .update(
            spreadsheetId=spreadsheet_id,
            valueInputOption="RAW",
            range=f"{sheet_name}!A{updated_row_index + 1}",
            body={
                "range": f"{sheet_name}!A{updated_row_index + 1}",
                "majorDimension": "ROWS",
                "values": [updated_row],
            },
        )
        .execute()
    )
    logger.debug(f"Update result: {result}")
    logger.debug(f"Updated record with sign out date: {updated_row}")


def append_row(sheets, spreadsheet_id: str, sheet_name: str, row) -> bool:
    """Append a row to the sheet with the specified values."""
    AppendResult = namedtuple("AppendResult", ["ok", "message"])
    result = (
        sheets.values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
            valueInputOption="RAW",
            body={"values": [row]},
        )
        .execute()
    )
    logger.debug(f"Append result: {result}")
    return AppendResult(ok=True, message="Successfully appended row")
