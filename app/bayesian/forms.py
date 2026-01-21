from ast import Str
from flask import Flask
from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    SubmitField,
    FieldList,
    FormField,
    SelectField,
    IntegerField,
    BooleanField,
)
from wtforms.validators import DataRequired, InputRequired

class BoSelectStation(FlaskForm):
    station = SelectField("Test string that hopefully allows to select the station", validators=[InputRequired()])
    submit = SubmitField("Submit Station")

class BoSelectMethod(FlaskForm):
    method = SelectField("Select method from station for optimization", [DataRequired()])
