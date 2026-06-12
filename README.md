## LABS: Laboratory Automation and Batch Scheduling

Welcome to this repository. It contains the current development state of the project  *"LABS: Laboratory Automation and Batch Scheduling"*. We intend to continue updating and improving this codebase.

### Setup

#### 1) Create a virtual environment
```bash
py3 -m venv .venv
```

#### 2) Install dependencies
```bash
pip install -r requirements.txt
```

#### 3) Start
```bash
flask db init
flask db migrate
flask db upgrade
```

#### 4) Start the Flask application
```bash
flask run
```

On first start, an administrative account is created automatically; the credentials are printed to the console.

#### 5) Usage

The optimizer script runs between the start and stop loop using the configured sleep time.

On the first run, the script creates an `Experiments` Excel file if one does not already exist. The workflow state is controlled by the `Flag` status in the first column of the Excel file:

- `FLAGGED` with parameters but no yield: post the experiment.
- `POSTED`: wait for a CSV file containing peak integrals. If the CSV exists, the yield and integrals are imported and the flag is set to `DONE`.
- `FLAGGED` without parameters: load previous experiments into the Bayesian optimizer and generate a new set of parameters.

After each step, a copy of the Excel file, and the CSV file if present, is moved to `Archive`. The active `Experiments` file must not be open while the optimizer is running.

To test a new reaction, move or delete the existing Excel file and update the parameters in `parameter.json`.

Conversion functions are defined in `parameter.json` and at the beginning of `optimizer.py`. They convert table values into backend values, for example converting Faraday to Coulomb.

**Login page:** http://127.0.0.1:5000/login

### Project status and security notes

This project is still under active development; some features are not yet implemented.

Since the API connection between the User-Interface and the Twisted Backend is not secured yet, we do not recommend use case open to the local or wide network under any circumstances yet. 
We tested the project, where the host for user interface and backend was the same computer. 
If an installation with several backends is to be realized at this time, we recommend putting the applications behind a reverse proxy and securing access with a firewall. 
We plan to secure the API communication for a later release. 

**CSRF protection:** `WTF_CSRF_ENABLED = False` in `config.py` disables CSRF protection. This is not recommended for production deployments.

## Dashboard

Start the dashboard with:

```bash
streamlit run dashboard.py
```

### Interpretability Plots

Generate the descriptor importance and interpretability plots with:

```bash
python scripts/interpretability.py
```

## Releases

- **0.1-beta** — Published with: *LABS: Laboratory Automation and Batch Scheduling – A Modular Open Source Python Program for the Control of Automated Electrochemical Synthesis with a Web Interface*  
  DOI: https://doi.org/10.1002/asia.202300380

- **0.1.1-beta** — Published with: *Automated Optimization of the Synthesis of Alkyl Arenesulfonates in an Undivided Electrochemical Flow Cell*  
  DOI: https://doi.org/10.1002/celc.202400360

- **0.2-beta** — Published with: *Bayesian Optimized Electrosynthesis of Azobenzenes in a Self- Optimizing Flow Set-up: from DoE-guidance to Gram-Scale Preparation*  
  DOI: https://doi.org/10.26434/chemrxiv-2026-8rd5c