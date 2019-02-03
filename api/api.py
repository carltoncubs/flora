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

:code`GET /settings` (requires authorisation)

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
from logging.config import dictConfig

dictConfig(
    {
        "version": 1,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
            }
        },
        "handlers": {
            "wsgi": {
                "class": "logging.StreamHandler",
                "stream": "ext://flask.logging.wsgi_errors_stream",
                "formatter": "default",
            }
        },
        "root": {"level": os.getenv("LOGLEVEL"), "handlers": ["wsgi"]},
    }
)

dotenv.load_dotenv()

app = Flask(__name__)
app.response_class.default_mimetype = "application/json"
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("SQLALCHEMY_DATABASE_URI")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")
app.config["JWT_ALGORITHM"] = "HS256"
app.config["GOOGLE_TOKEN_INFO"] = "https://www.googleapis.com/oauth2/v3/tokeninfo"

CORS(app)

models.db.init_app(app)


def init_db():
    models.db.create_all(app=app)


with app.app_context():
    try:
        init_db()
        print("Database initialised")
    except:
        print("Database already initialised")

    query = models.db.session.query(models.User).filter_by(
        email="nicholas.spain96@gmail.com"
    )

    # If there is nothing in the DB then insert me
    if not models.db.session.query(query.exists()).scalar():
        logger.info("Empty database, inserting Nick")
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
            app.logger.debug("Authorization: %s", request.headers.get("Authorization"))
            abort(400)
        token = match.group(1)
        app.logger.debug("Found authorization token: %s", token)
        try:
            payload = jwt.decode(
                token,
                app.config["JWT_SECRET_KEY"],
                algorithm=app.config["JWT_ALGORITHM"],
            )
            app.logger.debug("JWT payload: %s", payload)
        except (jwt.ExpiredSignatureError, jwt.DecodeError) as e:
            app.logger.info("Error verifying JWT: %s", e)
            abort(401)

        # Ensure that the user has been invited before allowing them
        # to continue
        email = payload.get("email")
        query = models.db.session.query(models.User).filter_by(email=email)
        if not models.db.session.query(query.exists()).scalar():
            app.logger.info("Login from uninvited user (email=%s)", email)
            abort(401)

        app.logger.debug("Login from user %s", email)
        return func(email, *args, **kwargs)

    return _wrap


def verify_google_access_token(access_token):
    """Verify access_token using the google token info endpoint."""
    uri = app.config.get("GOOGLE_TOKEN_INFO") + "?access_token=" + access_token
    resp = requests.get(uri)
    if not resp.ok:
        app.logger.info(f"Failed to get token info from {uri}")
        app.logger.debug("Body: %s", resp.content.decode("utf-8"))
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
        abort(401)

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
        app.config["JWT_SECRET_KEY"],
        algorithm=app.config["JWT_ALGORITHM"],
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
        app.logger.debug("Body: %s", data)
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

    settings = get_user_settings(email)
    if settings == None:
        app.logger.info("No settings exist for the user %s", email)
        return (jsonify({f"message": "No existing settings for user {email}"}), 400)

    worker.celery.send_task(
        "tasks.add_sign_in",
        args=(
            data["cubName"],
            data["cubSignature"],
            data["parentSignature"],
            time_,
            date_,
            settings.spreadsheet_id,
            settings.attendance_sheet,
        ),
    )

    return (
        jsonify(
            {
                "cubName": data["cubName"],
                "cubSignature": data["cubSignature"],
                "parentSignatureIn": data["parentSignature"],
                "timeIn": time_.isoformat(),
                "dateIn": time_.isoformat(),
            }
        ),
        204,
    )


@app.route("/v1/sign-out", methods=["POST"])
@jwt_required
def sign_out(email):
    try:
        data = request.get_json()
        app.logger.debug(f"Request body: {data}")
        time_ = datetime.datetime.strptime(data["time"], "%H:%M:%S").time()
        date_ = datetime.datetime.strptime(data["date"], "%Y-%m-%d").date()
    except BadRequest as e:
        app.logger.info("Error parsing JSON response body: %s", e)
        abort(400)
    except ValueError as e:
        app.logger.info("Error: %s", e)
        abort(400)

    settings = get_user_settings(email)
    if settings == None:
        app.logger.info("No settings exist for the user %s", email)
        return (jsonfiy({f"message": "No existing settings for user {email}"}), 400)

    worker.celery.send_task(
        "tasks.add_sign_out",
        args=(
            data["cubName"],
            data["parentSignature"],
            time_,
            date_,
            settings.spreadsheet_id,
            settings.attendance_sheet,
        ),
    )

    return (
        jsonify(
            {
                "cubName": data["cubName"],
                "parentSignatureOut": data["parentSignature"],
                "timeOut": time_.isoformat(),
                "dateOut": time_.isoformat(),
            }
        ),
        201,
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
        app.logger.info("Error parsing JSON request body: %s", e)
        abort(400)

    query = (
        models.db.session.query(models.Settings)
        .join(models.User)
        .filter(models.User.email == email)
    )

    if not models.db.session.query(query.exists()).scalar():
        user = models.db.session.query(models.User).filter_by(email=email).first()

        autocomplete_sheet = data.get("autocompleteSheet")
        if autocomplete_sheet is not None:
            autocomplete_sheet = autocomplete_sheet.strip()

        user_settings = models.Settings(
            spreadsheet_id=data["spreadsheetId"].strip(),
            attendance_sheet=data["attendanceSheet"].strip(),
            user_id=user.id,
            autocomplete_sheet=autocomplete_sheet,
        )

        models.db.session.add(user_settings)
        models.db.session.commit()
        app.logger.debug("New settings: %s", user_settings)

        return jsonify(data), 201

    user = models.db.session.query(models.User).filter_by(email=email).first()

    user_settings = query.first()
    user_settings.spreadsheet_id = data["spreadsheetId"].strip()
    user_settings.attendance_sheet = data["attendanceSheet"].strip()
    autocomplete_sheet = data.get("autocompleteSheet")
    if autocomplete_sheet is not None:
        autocomplete_sheet = autocomplete_sheet.strip()
    user_settings.autocomplete_sheet = autocomplete_sheet
    user_settings.user_id = user.id
    app.logger.debug("Updated settings: %s", user_settings)
    models.db.session.commit()

    return jsonify(data)


@app.route("/v1/names", methods=["GET"])
@jwt_required
def names(email):
    user = models.db.session.query(models.User).filter_by(email=email).first()
    name_records = models.db.session.query(models.Name).fitler_by(user_id=user.id).all()
    return jsonify({"names": [name_record.name for name_record in name_records]})


def get_user_settings(email):
    return (
        models.db.session.query(models.Settings)
        .join(models.User)
        .filter_by(email=email)
        .first()
    )


if __name__ == "__main__":
    app.run(debug=True)
