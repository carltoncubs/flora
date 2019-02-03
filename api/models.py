from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cub_name = db.Column(db.String(80), nullable=False)
    cub_signature_in = db.Column(db.Text())
    parent_signature_in = db.Column(db.Text())
    parent_signature_out = db.Column(db.Text())
    time_in = db.Column(db.Time())
    date_in = db.Column(db.Date())
    time_out = db.Column(db.Time())
    date_out = db.Column(db.Date())

    def __str__(self):
        return f"Attendance(id={self.id}, cub_name={self.cub_name})"


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    token = db.Column(db.Text, nullable=True, unique=True)

    def __str__(self):
        return f"User(id={self.id}, name={self.name})"



class Settings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    spreadsheet_id = db.Column(db.String(300), nullable=False)
    attendance_sheet = db.Column(db.String(120), nullable=False)
    autocomplete_sheet = db.Column(db.String(120))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    def __str__(self):
        return f"Settings(id={self.id}, user_id={self.user_id})"

class Name(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    def __str__(self):
        return f"Name(id={self.id}, name={self.name})"
