"""REST API backend for the Cub Attendance web app.

Overview
========

The intended use of this API is as follows; (1) retrieve a JWT using
:code:`POST /auth/google`, (2) utilise the other endpoints. The JWT
request must use the access token retrieved from a Google OAuth2 sign
in. This token will be validated. We will then check that the user is
authorised to use the application. If they are, then a JWT is
returned.

Endpoints
=========

All endpoints expect the body to be JSON and will respond with JSON
themselves.

:code:`POST /auth/google`

Endpoint used to get a JWT for authorisation to use other
endpoints. The body of the request should contain the access token
retrieved from using Google OAuth2 ("accessToken") and the Google
project object from Google OAuth2 ("user"). Once the access token is
verified and it is checked that the user is on the invite list, the
endpoint will respond with a JWT ("token"). This can be used in the
Authorization header as a Bearer token (:code:`Authorization: Bearer
<token>`).

:code:`POST /sign-in` (requires authorisation)

Endpoint used to submit sub sign ins. It expects "cubName",
"cubSignature" and "parentSignature" in the request body.

:code:`POST /sign-out` (requires authorisation)

Endpoint used to submit sub sign outs. It expects "cubName" and
"parentSignature" in the request body.

:code`GET /settings` (reuqires authorisation)

Endpoint used to get previously set settings. The response body will
contain the keys; "spreadsheetId", "attendanceSheet",
"autocompleteSheet". In the event that there are not existing settings,
the all the fields will be empty.

:code`POST /settings` (requires authorisation)

Endpoint used to submit new settings. The request body must contain
"spreadsheetId" and "attendanceSheet". "autocompleteSheet" may
optionally be included.

:code`GET /names` (requires authorisation)

Endpoint used to get a list of the names of all the cubs that have
previously signed in and names in the autocomplete sheet. The respone
body will contain the key "name" which corresponds to the list of
names returned.

"""

import datetime
import os
import re
import time
from functools import wraps

import requests

import dotenv
import jwt
import models
import worker
from flask import Flask, abort, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import BadRequest

dotenv.load_dotenv()

app = Flask(__name__)
app.response_class.default_mimetype = "application/json"
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("SQLALCHEMY_DATABASE_URI")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.urandom(20)
app.config["JWT_SECRET_KEY"] = os.urandom(20)
app.config["JWT_ALGORITHM"] = "HS256"
app.config["GOOGLE_TOKEN_INFO"] = "https://www.googleapis.com/oauth2/v3/tokeninfo"

CORS(app)

models.db.init_app(app)

def init_db():
    models.db.create_all(app=app)

@app.cli.command("initdb")
def initdb_cmd():
    """Initialise DB."""
    init_db()
    print("Initialised database.")

with app.app_context():
    query = models.db.session.query(models.User).filter_by(
        email="nicholas.spain96@gmail.com"
    )
    if not models.db.session.query(query.exists()).scalar():
        user = models.User(name="Nicholas Spain", email="nicholas.spain96@gmail.com")
        models.db.session.add(user)
        models.db.session.commit()


def jwt_required(func):
    """Verify and decode a JWT Bearer token in the Authorization
    header.

    """

    @wraps(func)
    def _wrap(*args, **kwargs):
        app.logger.debug("Verifying JWT")
        match = re.search("Bearer (.+)", request.headers.get("Authorization", ""))
        if match is None:
            app.logger.info("Could not find JWT in Authorization header")
            abort(400)
        token = match.group(1)

        try:
            payload = jwt.decode(
                token,
                app.config["JWT_SECRET_KEY"],
                algorithm=app.config["JWT_ALGORITHM"],
            )
        except (jwt.ExpiredSignatureError, jwt.DecodeError) as e:
            app.logger.info("Error verifying JWT: %s", e)
            abort(401)

        email = payload.get("email")
        query = models.db.session.query(models.User).filter_by(email=email)
        if not models.db.session.query(query.exists()).scalar():
            app.logger.info("Login from uninvited user (email=%s)", email)
            abort(401)

        return func(email, *args, **kwargs)

    return _wrap


def verify_google_access_token(access_token):
    uri = app.config.get("GOOGLE_TOKEN_INFO") + "?access_token=" + access_token
    resp = requests.get(uri)
    if not resp.ok:
        app.logger.info(f"Failed to get token info from {uri}")
        return None

    info = resp.json()
    exp = time.gmtime(int(info["exp"]))
    now = time.gmtime()

    if exp <= now:
        app.logger.info(f"{access_token} is an expired token")
        return None

    return info


@app.route("/v1/auth/google", methods=["POST"])
def google_auth():
    try:
        access_token = request.get_json()["accessToken"]
    except BadRequest:
        app.logger.info("Received badly formatted json")
        app.logger.debug("Body: %s", request.data)
        abort(400)
    except TypeError:
        app.logger.info("'accessToken' is not in request body")
        app.logger.debug("Body: %s", request.get_json())
        abort(400)

    user = verify_google_access_token(access_token)
    if user is None:
        app.logger.info("access token (%s) is invalid", access_token)
        abort(400)

    email = user["email"]
    query = models.db.session.query(models.User).filter_by(email=email)
    if not models.db.session.query(query.exists()).scalar():
        app.logger.info("Attempted login with email %s not invited", email)
        abort(401)

    token = jwt.encode(
        {
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
            "email": email,
        },
        app.config.get("JWT_SECRET_KEY"),
        algorithm="HS256",
    )

    user = models.db.session.query(models.User).filter_by(email=email).first()
    user.token = token
    models.db.session.commit()

    return jsonify({"token": token.decode("utf-8")})


@app.route("/v1/sign-in", methods=["POST"])
@jwt_required
def sign_in(email):
    try:
        data = request.get_json()
        time_ = datetime.datetime.strptime(data["time"], "%H:%M:%S").time()
        date_ = datetime.datetime.strptime(data["date"], "%Y-%m-%d").date()
    except BadRequest as e:
        app.logger.info("Error decoding json: %s", e)
        app.logger.deubg("Body: %s", request.data)
        abort(400)
    except ValueError as e:
        app.logger.info("Error: %s", e)
        app.logger.debug("Body: %s", data)
        abort(400)

    attendance = models.Attendance(
        cub_name=data["cubName"],
        cub_signature_in=data["cubSignature"],
        parent_signature_in=data["parentSignature"],
        time_in=time_,
        date_in=date_,
    )

    models.db.session.add(attendance)
    models.db.session.commit()

    return (
        jsonify(
            {
                "cubName": attendance.cub_name,
                "cubSignature": attendance.cub_signature_in,
                "parentSignatureIn": attendance.parent_signature_in,
                "timeIn": attendance.time_in.isoformat(),
                "dateIn": attendance.date_in.isoformat(),
            }
        ),
        204,
    )


@app.route("/v1/sign-out", methods=["POST"])
@jwt_required
def sign_out(email):
    try:
        data = request.get_json()
        time_ = datetime.datetime.strptime(data["time"], "%H:%M:%S").time()
        date_ = datetime.datetime.strptime(data["date"], "%Y-%m-%d").date()
    except BadRequest as e:
        app.logger.info("Error parsing JSON response body: %s", e)
        abort(400)
    except ValueError as e:
        app.logger.info("Error: %s", e)
        abort(400)

    settings = (
        models.db.session.query(models.Settings)
        .join(models.User)
        .filter_by(email=email)
        .first()
    )

    query = (
        models.db.session.query(models.Attendance)
        .filter_by(cub_name=data["cubName"])
        .order_by(models.Attendance.date_in)
    )

    if not models.db.session.query(query.exists()).scalar():
        app.logger.info(f"No matching sign in record found for %s", data["cubName"])

        attendance = models.Attendance(
            cub_name=data["cubName"],
            parent_signature_out=data["parentSignature"],
            time_out=time_,
            date_out=date_,
        )
        models.db.session.add(attendance)
        models.db.session.commit()

        worker.celery.send_task(
            "tasks.record_attendance",
            args=[settings.spreadsheet_id, settings.sheet_name],
            kwargs={
                "cub_name": attendance.cub_name,
                "parent_signature_out": attendance.parent_signature_out,
                "time_out": attendance.time_out.isoformat(),
                "date_out": attendance.date_out.isoformat(),
            },
        )

        return (
            jsonify(
                {
                    "cubName": attendance.cub_name,
                    "parentSignatureOut": attendance.parent_signature_out,
                    "timeOut": attendance.time_out.isoformat(),
                    "dateOut": attendance.date_out.isoformat(),
                }
            ),
            201,
        )

    attendance = query.first()
    attendance.parent_signature_out = data["parentSignature"]
    attendance.time_out = time_
    attendance.date_out = date_
    models.db.session.commit()

    worker.celery.send_task(
        "tasks.record_attendance",
        args=[],
        kwargs={
            "cub_name": attendance.cub_name,
            "cub_signature_in": attendance.cub_signature_in,
            "parent_signature_in": attendance.parent_signature_in,
            "parent_signature_out": attendance.parent_signature_out,
            "time_in": attendance.time_in.isoformat(),
            "date_in": attendance.date_out.isoformat(),
            "time_out": attendance.time_out.isoformat(),
            "date_out": attendance.date_out.isoformat(),
        },
    )

    return jsonify(
        {
            "cubName": attendance.cub_name,
            "cubSignatureIn": attendance.cub_signature_in,
            "parentSignatureIn": attendance.parent_signature_in,
            "parentSignatureOut": attendance.parent_signature_out,
            "timeIn": attendance.time_in.isoformat(),
            "dateIn": attendance.date_out.isoformat(),
            "timeOut": attendance.time_out.isoformat(),
            "dateOut": attendance.date_out.isoformat(),
        }
    )


@app.route("/v1/settings", methods=["GET"])
@jwt_required
def settings_get(email):
    query = (
        models.db.session.query(models.Settings)
        .join(models.User, models.User.id == models.Settings.user_id)
        .filter(models.User.email == email)
    )

    if not models.db.session.query(query.exists()).scalar():
        app.logger.info("No settings found for user (email=%s)", email)
        return jsonify(
            {"spreadsheetId": "", "attendanceSheet": "", "autocompleteSheet": ""}
        )

    settings = query.first()
    return jsonify(
        {
            "spreadsheetId": settings.spreadsheet_id,
            "attendanceSheet": settings.attendance_sheet,
            "autocompleteSheet": settings.autocomplete_sheet,
        }
    )


@app.route("/v1/settings", methods=["POST"])
@jwt_required
def settings_post(email):
    try:
        data = request.get_json()
    except BadRequest as e:
        app.logger.info("Error parseing JSON request body: %s", e)
        abort(400)

    query = (
        models.db.session.query(models.Settings)
        .join(models.User)
        .filter(models.User.email == email)
    )

    if not models.db.session.query(query.exists()).scalar():
        user = models.db.session.query(models.User).filter_by(email=email).first()
        user_settings = models.Settings(
            spreadsheet_id=data["spreadsheetId"],
            attendance_sheet=data["attendanceSheet"],
            autocomplete_sheet=data.get("autocompleteSheet"),
            user_id=user.id,
        )

        models.db.session.add(user_settings)
        models.db.session.commit()
        app.logger.debug("New settings: %s", user_settings)

        return jsonify(data), 201

    user = models.db.session.query(models.User).filter_by(email=email).first()

    user_settings = query.first()
    user_settings.spreadsheet_id = data["spreadsheetId"]
    user_settings.attendance_sheet = data["attendanceSheet"]
    user_settings.autocomplete_sheet = data.get("autocompleteSheet")
    user_settings.user_id = user.id
    app.logger.debug("Updated settings: %s", user_settings)
    models.db.session.commit()

    return jsonify(data)


@app.route("/v1/names", methods=["GET"])
@jwt_required
def names(email):
    return jsonify(
        {"names": [row.names for row in models.db.session.query(models.Names).all()]}
    )


if __name__ == "__main__":
    app.run(debug=True)
