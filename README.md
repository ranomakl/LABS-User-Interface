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

#### 3) Initialize and migrate the database
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

**Login page:** http://127.0.0.1:5000/login

### Project status and security notes

This project is still under active development; some features are not yet implemented.

Since the API connection between the User-Interface and the Twisted Backend is not secured yet, we do not recommend use case open to the local or wide network under any circumstances yet. 
We tested the project, where the host for user interface and backend was the same computer. 
If an installation with several backends is to be realized at this time, we recommend putting the applications behind a reverse proxy and securing access with a firewall. 
We plan to secure the API communication for a later release. 

**CSRF protection:** `WTF_CSRF_ENABLED = False` in `config.py` disables CSRF protection. This is not recommended for production deployments.

## Releases

- **0.1-beta** — Published with: *LABS: Laboratory Automation and Batch Scheduling – A Modular Open Source Python Program for the Control of Automated Electrochemical Synthesis with a Web Interface*  
  DOI: https://doi.org/10.1002/asia.202300380

- **0.1.1-beta** — Published with: *Automated Optimization of the Synthesis of Alkyl Arenesulfonates in an Undivided Electrochemical Flow Cell*  
  DOI: https://doi.org/10.1002/celc.202400360