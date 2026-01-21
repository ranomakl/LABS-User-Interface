#import imp
from typing import ParamSpecKwargs
from flask import (
    Blueprint,
    render_template,
    url_for,
    redirect,
    flash,
    request,
    app,
    abort,
    Response,
)

import time
from flask_login import login_user, logout_user, login_required, current_user
from app.auth.models import User
from app.experiments.models import ExperimentalStation
from app.experiments.forms import NewExperimentalDesign
import requests
from requests.auth import HTTPBasicAuth
from werkzeug.utils import secure_filename
import pathlib
from flask import jsonify
from .. import db
import json
from datetime import datetime
from werkzeug.exceptions import HTTPException
from requests.exceptions import HTTPError
import os.path, time
from .forms import (
    BoSelectStation
)
from app.bayesian.optimizer import start_bo_loop

bayesian_blueprint = Blueprint("bayesian", __name__)




@bayesian_blueprint.route("/bayesian_overview", methods=["GET", "POST"])
def bayesian_overview():
    """Generates Device Overview, requests deviece list from API call. Uses experiments database information for list of devices and  adresses.

    Returns:
        render_template: renders a page for the user with Device information overview.
    """
    availableStations = ExperimentalStation.get_all_stations()

    if len(availableStations) == 0:
        flash("No stations available. Create a station in station list.", "warning")
        return redirect(url_for("experiments.list_station"))
    else:
        station_list = [(station.id, station.name) for station in availableStations]
    #form = NewExperimentalDesign(request.form)
    form = BoSelectStation(request.form)
    form.station.choices = station_list
    #form = BoSelectStation(request.form)
    #station_list = ExperimentalStation.query.all()
    #form.station.choices = station_list

    return render_template("bayesian/overview.html", form=form)


@bayesian_blueprint.route("/bayesian", methods=["POST"])
def bo_select_station():
    #form = BoSelectStation(request.form)
    toPrint = request.form.get('station')
    flash("TestFlash!"+str(toPrint),"warning")

    return render_template("bayesian/station/"+str(toPrint))
    #return bayesian_overview()

@bayesian_blueprint.route("/bayesian/station/<int:station_id>", methods=["GET", "POST"])
def bo_select_method():
    pass




@bayesian_blueprint.route("/run_bayesian", methods=["GET", "POST"])
@login_required
def TestPrint():
    # stage_id = request.form["stage_id"]
    if request.cookies:
        print(request.cookies)
        start_bo_loop(request.cookies)
        time.sleep(0.25)
        return redirect('http://127.0.0.1:5000/overview')
    else:
        return "",204


# def get_station_list():
#     """Frontend API helper function endpoint that returns a list of all stations that a user can observe.
#     The datasource is the Stations Model from experiments blueprint."""
#     stations = ExperimentalStation.query.all()
#     return stations


