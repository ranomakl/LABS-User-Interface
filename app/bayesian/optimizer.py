import os
import json
import pandas as pd
import numpy as np
import time
import threading
import shutil
import torch
from datetime import datetime
from botorch.models.transforms import Normalize, Standardize

# from botorch.models.transforms.outcome import Standardize
from botorch.utils.sampling import draw_sobol_samples
from botorch.models import SingleTaskGP
from botorch.acquisition import (
    UpperConfidenceBound,
    ExpectedImprovement,
    LogExpectedImprovement,
    qUpperConfidenceBound,
    qMaxValueEntropy,
    qUpperConfidenceBound,
)
from botorch.optim import optimize_acqf
from botorch.fit import fit_gpytorch_mll
from gpytorch.mlls import ExactMarginalLogLikelihood
import requests

DEFAULT_COLUMNS = [
    "Flag",
    "Yield",
    "Conversion",
    "Selectivity",
    "I_ref",
    "N_nuclei_ref",
    "n_ref/mmol",
    "I_SM",
    "N_nuclei_SM",
    "n_SM/mmol",
    "I_prod",
    "N_nuclei_prod",
]

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
dtype = torch.float64

torch.set_default_dtype(torch.float64)

# If True, will not start an experiment
# but just check we can predict the next point
DEBUG = False

def create_GP(train_X, train_Y, bounds=None):
    """
    Implements a simple training procedure for a GuassianProcess model.
    Trained by optimizing the exact marginal log likelihood (evidence).
    """
    feature_dim = train_X.shape[-1] # number of experimental settings

    # shape should be bounds.size == (2,d)
    if bounds.size(-1) != feature_dim:
        bounds = bounds.T

    gp = SingleTaskGP(
        train_X=train_X,
        train_Y=train_Y,
        # Standarize output, normalize input
        outcome_transform=Standardize(m=1),
        input_transform=Normalize(d=feature_dim, bounds=bounds),
    )
    mll = ExactMarginalLogLikelihood(gp.likelihood, gp)
    mll = fit_gpytorch_mll(mll)
    return gp, gp.likelihood

# Define the converter functions for the parameter here
def converter_applied_charge(dict_of_parameters):
    return round(
        dict_of_parameters["applied_charge"]
        * 96485
        * dict_of_parameters["nitro_concentration"]
        * dict_of_parameters["volume"]
        * 0.001,
        0,
    )


def converter_DMSO_equivalents(dict_of_parameters):
    return round(
        dict_of_parameters["DMSO_equivalents"]
        * dict_of_parameters["nitro_concentration"]
        * dict_of_parameters["volume"]
        * 1
        / 9,
        3,
    )


def converter_SE_concentration(dict_of_parameters):
    return round(
        (dict_of_parameters["SE_concentration"] - 0.15)
        * dict_of_parameters["volume"]
        * 1
        / 0.3,
        3,
    )


def converter_nitro_concentration(dict_of_parameters):
    return round(
        dict_of_parameters["nitro_concentration"]
        * dict_of_parameters["volume"]
        * 1
        / 1.2,
        3,
    )


def calculate_methanolvolume(dict_of_parameters):
    return (
        dict_of_parameters["volume"]
        - dict_of_parameters["DMSO_equivalents"]
        - dict_of_parameters["SE_concentration"]
        - dict_of_parameters["nitro_concentration"]
    )


def bayesian_optimization_loop(cookies):
    # Defining stop_loop to stop the loop by calling a function
    global stop_loop
    stop_loop = False
    path = "app/bayesian/"

    parameter_input_file_name = "parameter"
    bo_config_file_name = "optimizer_config"
    experiments_input_file_name = "Experiments"
    archiv_folder_name = "Archive"

    output_dir = os.path.join(os.getcwd(), path)
    os.makedirs(output_dir, exist_ok=True)
    parameter_input_path = os.path.join(output_dir, parameter_input_file_name + ".json")
    # print(parameter_input_path)
    with open(parameter_input_path, "r") as f:
        parameter_input = json.load(f)
    # print(parameter_input)

    bo_config_input_path = os.path.join(output_dir, bo_config_file_name + ".json")
    with open(bo_config_input_path, "r") as f:
        bo_config_input = json.load(f)
    # print(bo_config_input)

    experiments_input_path = os.path.join(
        output_dir, experiments_input_file_name + ".xlsx"
    )
    # Check if the Excel file exists, and create it if it does not exist
    if not os.path.exists(experiments_input_path):
        # Create a new DataFrame with the required columns
        columns = DEFAULT_COLUMNS + [key for key in parameter_input.keys()]
        new_df = pd.DataFrame(columns=columns)

        # Save the new DataFrame to the Excel file
        new_df.to_excel(experiments_input_path, index=False, engine="openpyxl")
        print(f"Created new Excel file at {experiments_input_path}")

    # Defines Path of the csv file
    integral_csv_file_folder = os.path.join(os.getcwd(), path)

    archive_folder_path = os.path.join(output_dir, archiv_folder_name)
    os.makedirs(archive_folder_path, exist_ok=True)

    # for reproducibility
    torch.manual_seed(0)
    np.random.seed(0)

    counter = 1
    print("Loop started")

    while stop_loop == False:
        # Load initial experiments dats sheet form excel
        input_exp_df = pd.read_excel(experiments_input_path, engine="openpyxl")

        number_of_flags = input_exp_df.iloc[:, 0].notnull().sum()
        number_of_yields = input_exp_df.iloc[:, 1].notnull().sum()
        number_of_parameter = input_exp_df.iloc[:, 12].notnull().sum()

        # Helper parameter for if conditions
        df_parameter_only = input_exp_df.iloc[:, 12:]
        # print(df_parameter_only)

        if number_of_yields not in input_exp_df.index:
            print("All experiments are done")
            stop_bo_loop()

        # Checks for rows that are have a flag that is not POSTED and the number of Yields and Conditions is not the same
        elif (
            number_of_flags > number_of_yields
            and input_exp_df.loc[number_of_yields, "Flag"] != "POSTED"
            and number_of_yields < number_of_parameter
        ):
            # Construct the string for posting the Conditions to the Station
            s = requests.Session()
            request_url = f"http://127.0.0.1:11123/api/add_experiment"

            # makes the line that needs to be posted a dict
            dict_of_parameters = df_parameter_only.iloc[number_of_yields].to_dict()
            # print(dict_of_parameters)

            # constructs a dictionary with the parameters and the converter funcions is existing
            dict_of_converter_functions = {
                key: value["convert"]
                for key, value in parameter_input.items()
                if "convert" in value and value["convert"] is not None
            }
            # print(dict_of_converter_functions)

            function_registry = {}
            for key, func_name in dict_of_converter_functions.items():
                func = globals().get(func_name)
                if callable(func):
                    function_registry[key] = func
                else:
                    raise ValueError(
                        f"Function '{func_name}' for key '{key}' is not defined or not callable."
                    )

            for key, func in function_registry.items():
                if key in dict_of_parameters:
                    dict_of_parameters[key] = func(dict_of_parameters)

            # Changes the keys in dict_of_parameters to the cfg_names from the parameter.json if cfg_name is not null
            dict_of_converted_parameters = {
                (
                    parameter_input[key]["cfg_name"]
                    if parameter_input[key].get("cfg_name") is not None
                    else key
                ): value
                for key, value in dict_of_parameters.items()
                if key in parameter_input
            }

            # print(f"Converted parameters: {dict_of_converted_parameters}")

            result = {
                "experiment_id": f"Experiment-{number_of_yields + 1}",
                "experiment_type": bo_config_input["experiment_type"],
                **dict_of_converted_parameters,
            }
            print(f"Posted:{result}")

            r = s.post(request_url, data=result,cookies=cookies)
            r.raise_for_status()

            # Extract molarities where limiting_reagent is set to true, calculate n_SM in mmol and update the DataFrame
            limiting_reagent_molarities = {
                key: value["molarity"]
                for key, value in parameter_input.items()
                if value.get("limiting_reagent") is True and "molarity" in value
            }

            # Check if there is exactly one limiting reagent molarity
            if len(limiting_reagent_molarities) != 1:
                raise ValueError(
                    "There must be exactly one limiting reagent molarity. Found: {}".format(
                        len(limiting_reagent_molarities)
                    )
                )
            # print(f"Limiting reagent molarities: {limiting_reagent_molarities}")

            limiting_key = next(iter(limiting_reagent_molarities), None)
            # print(f"Limiting key: {limiting_key}")
            volume_key = (
                parameter_input[limiting_key]["cfg_name"] if limiting_key else None
            )
            # print(f"Volume key: {volume_key}")
            volume = dict_of_converted_parameters.get(volume_key)
            # print(f"Volume: {volume}")
            if volume is None:
                raise KeyError(
                    f"Volume key '{volume_key}' not found in dict_of_converted_parameters"
                )

            # Calculate n_SM in mmol from the first (only) value in limiting_reagent_molarities (molarity of the limiting reagent) and the posted volume of it
            # n_sm = next(iter(limiting_reagent_molarities.values())) * dict_of_converted_parameters[next(iter(limiting_reagent_molarities))]
            n_sm = limiting_reagent_molarities[limiting_key] * volume
            input_exp_df.loc[number_of_yields, "n_SM/mmol"] = n_sm

            # write the number of nmr active nuclei for ref, prod and SM in the excel sheet
            input_exp_df.loc[number_of_yields, "N_nuclei_ref"] = bo_config_input[
                "number_of_nuclei_ref"
            ]
            input_exp_df.loc[number_of_yields, "N_nuclei_SM"] = bo_config_input[
                "number_of_nuclei_SM"
            ]
            input_exp_df.loc[number_of_yields, "N_nuclei_prod"] = bo_config_input[
                "number_of_nuclei_prod"
            ]
            input_exp_df.loc[number_of_yields, "n_ref/mmol"] = bo_config_input[
                "n_ref/mmol"
            ]

            # Sets the Flag to POSTED and writes the data in the Excel Sheet
            input_exp_df.loc[number_of_yields, input_exp_df.columns[0]] = "POSTED"
            input_exp_df.to_excel(
                experiments_input_path, index=False, engine="openpyxl"
            )

            # Makes a new, not updated Excel file that is readable while the loop is running
            path_readable_excel_file = f"{archive_folder_path}\\post_experiment_{datetime.now().strftime('%H%M%S_%d%m%Y')}.xlsx"
            input_exp_df.to_excel(
                path_readable_excel_file, index=False, engine="openpyxl"
            )
            # also save as csv
            input_exp_df.to_csv(
                os.path.join(
                    archive_folder_path,
                    f"post_experiment_{datetime.now().strftime('%H%M%S_%d%m%Y')}.csv",
                ),
                index=False,
            )

        # Checks for the Conditions that has been posted and reads the output csv from MestRe Nova, puts the yield in the main table and sets the Flag to DONE
        elif input_exp_df.loc[number_of_yields, "Flag"] == "POSTED":
            # Makes a list of all csv files in folder, used to avoid making them name sensitive
            try:
                csv_files_list_in_folder = [
                    f
                    for f in os.listdir(integral_csv_file_folder)
                    if f.endswith(".csv")
                ]

                for csv_file_in_folder in csv_files_list_in_folder:
                    integral_csv_file_path = os.path.join(
                        integral_csv_file_folder, csv_file_in_folder
                    )

                    # Reads the cvs File with the Integrals from MestRe Nova
                    integral_csv_df = pd.read_csv(
                        integral_csv_file_path, sep=",", header=None
                    )
                    # print(integral_csv_df)

                    integral_csv_df.columns = ["Name", "Integral"]
                    max_integral_values = integral_csv_df.groupby("Name")[
                        "Integral"
                    ].max()

                    # Initializes the Parameter to calculate yield, conversion and selectivity
                    integral_sm = max_integral_values.get("Starting Material", 0)
                    # print(f"Starting Material: {integral_sm}")

                    integral_prod = max_integral_values.get("Product Integral", 0)
                    # print(f"Product Integral: {integral_prod}")

                    integral_ref = max_integral_values.get("Internal Standard", 0)
                    if integral_ref == 0:
                        print(
                            "No Internal Standard found in the csv file. Please check the csv file."
                        )
                    # print(f"Internal Standard: {integral_ref}")

                    N_nuclei_ref = input_exp_df.loc[number_of_yields, "N_nuclei_ref"]
                    N_nuclei_SM = input_exp_df.loc[number_of_yields, "N_nuclei_SM"]
                    N_nuclei_prod = input_exp_df.loc[number_of_yields, "N_nuclei_prod"]

                    n_ref = input_exp_df.loc[number_of_yields, "n_ref/mmol"]
                    n_sm = input_exp_df.loc[number_of_yields, "n_SM/mmol"]

                    # Calculate Yield, Conversion, Selectivity and adds them to the table
                    reaction_yield = (
                        100
                        * n_ref
                        * integral_prod
                        * N_nuclei_ref
                        / (n_sm * integral_ref * N_nuclei_prod)
                    )
                    input_exp_df.loc[number_of_yields, "Yield"] = reaction_yield
                    reaction_conversion = (
                        100
                        - 100
                        * n_ref
                        * integral_sm
                        * N_nuclei_SM
                        / (N_nuclei_SM * integral_ref * n_sm)
                    )
                    input_exp_df.loc[number_of_yields, "Conversion"] = (
                        reaction_conversion
                    )
                    reaction_selectivity = 100 * reaction_yield / reaction_conversion
                    input_exp_df.loc[number_of_yields, "Selectivity"] = (
                        reaction_selectivity
                    )

                    # Writing the csv Data and the calculated data in the Excel Sheet
                    input_exp_df.loc[number_of_yields, input_exp_df.columns[4]] = (
                        integral_ref
                    )
                    input_exp_df.loc[number_of_yields, input_exp_df.columns[7]] = (
                        integral_sm
                    )
                    input_exp_df.loc[number_of_yields, input_exp_df.columns[10]] = (
                        integral_prod
                    )
                    input_exp_df.loc[number_of_yields, input_exp_df.columns[0]] = "DONE"
                    input_exp_df.to_excel(
                        experiments_input_path, index=False, engine="openpyxl"
                    )

                    # Moves the .csv file to the Archive folder
                    new_name_for_csv_file = (
                        str(number_of_yields + 1) + csv_file_in_folder
                    )
                    shutil.move(
                        integral_csv_file_path,
                        os.path.join(archive_folder_path, new_name_for_csv_file),
                    )

                    # Makes a new, not continiusly updated Excel file that is readable while the loop is running
                    path_readable_excel_file = f"{archive_folder_path}\\read_csv_{datetime.now().strftime('%H%M%S_%d%m%Y')}.xlsx"
                    input_exp_df.to_excel(path_readable_excel_file, index=False)

            except:
                continue
                # print("Error while creating the list of csv files.")

        # Checks if there are more flags than Conditions and number of yields == number of conditions, then do the BO and write new Conditions
        elif (
            number_of_flags > number_of_parameter
            and number_of_yields == number_of_parameter
        ):
            print("\n" + "-" * 60)
            print("Starting BO at iteration", number_of_parameter)
            print("-" * 60)
            # Define which parameters to include from Excel sheet
            all_param_columns = list(
                input_exp_df.columns[12:]
            )  # param columns start from 13th
            active_param_columns = [
                col
                for col in all_param_columns
                if not parameter_input.get(col, {}).get("excluded_from_BO", False)
            ]
            # print(f"Active parameters for BO: {active_param_columns}")

            # Strips the Data that is used for the bayesian optimization and converts it to a tensor
            init_experiments_np = input_exp_df.loc[
                : number_of_yields - 1, active_param_columns
            ].to_numpy(dtype=float)
            init_experiments = torch.tensor(init_experiments_np, dtype=torch.double)

            # Extract the bounds for normalization and convert to tensor
            param_bounds = [
                (parameter_input[col]["min"], parameter_input[col]["max"])
                for col in active_param_columns
                if parameter_input[col]["type"] == "continuous"
            ]
            bounds = torch.tensor(param_bounds, dtype=torch.double).T  # shape: [2, d]

            # Load and normalize yields (0-100 %)->(0-1)
            init_yields_percent = torch.tensor(
                input_exp_df.loc[: number_of_yields - 1, "Yield"].to_numpy(),
                dtype=torch.double,
            )
            # init_yields = (init_yields_percent / 100.0).unsqueeze(-1).double()
            init_yields = init_yields_percent.unsqueeze(-1).double()

            print("bounds\n", bounds)

            model, likelihood = create_GP(init_experiments, init_yields, bounds=bounds)
            # noise_var = likelihood.noise_covar.noise.item()

            # Define the acquisiton fun
            candidate_set = draw_sobol_samples(bounds=bounds, n=10_000, q=1).squeeze(1)
            decayed_beta = bo_config_input["beta"] * (
                1 - bo_config_input["beta_decay"]
            ) ** (number_of_yields - bo_config_input["beta_decay_delay"])
            if bo_config_input["acquisition_function"] in [
                "UpperConfidenceBound",
                "UCB",
            ]:
                print(f"UpperConfidenceBound with Beta = {decayed_beta}")
                acquisition_function = UpperConfidenceBound(
                    model, beta=decayed_beta, maximize=True
                )
            elif bo_config_input["acquisition_function"] in [
                "ExpectedImprovement",
                "EI",
            ]:
                # acquisition_function = ExpectedImprovement(model, best_f=1.0, maximize=True)
                acquisition_function = ExpectedImprovement(
                    model, best_f=init_yields.max(), maximize=True
                )
            elif bo_config_input["acquisition_function"] in [
                "LogExpectedImprovement",
                "LogEI",
            ]:
                acquisition_function = LogExpectedImprovement(
                    model, best_f=init_yields.max(), maximize=True
                )
                # acquisition_function = LogExpectedImprovement(model, best_f=1.0, maximize=True)
            elif bo_config_input["acquisition_function"] in [
                "qUpperConfidenceBound",
                "qUCB",
            ]:
                print(f"qUpperConfidenceBound with Beta = {decayed_beta}")
                acquisition_function = qUpperConfidenceBound(model, beta=decayed_beta)
            elif bo_config_input["acquisition_function"] in ["qMaxValueEntropy", "qME"]:
                acquisition_function = qMaxValueEntropy(
                    model, candidate_set, maximize=True
                )
            else:
                raise ValueError(
                    f"Unknown acquisition function: {bo_config_input['acquisition_function']}"
                )

            # Optimize the acquisition function to find the next point
            # q defines how many points to suggest at once
            # num_restarts defines how many times the optimization is restarted to find the best point
            # raw_samples defines how many samples are drawn from the acquisition function to find the best point
            next_point, acq_value = optimize_acqf(
                acq_function=acquisition_function,
                bounds=bounds,
                q=1, # predict one experimental setting at a time
                num_restarts=100,
                raw_samples=512,
            )

            # Convert the tensor to a list and remove the batch dimension
            next_point = next_point.detach().squeeze(0).tolist()

            param_dict = dict(zip(active_param_columns, next_point))
            # print(f"Suggested parameters (before conversion): {param_dict}")

            # Create a dictionary with all keys from parameter_input and value None
            next_point = {key: None for key in parameter_input.keys()}
            # print(f"next point:{next_point}")

            print("\nModel suggested next point:")
            for key in next_point:
                if key in param_dict:
                    next_point[key] = param_dict[key]
                    print(f"{key}: {next_point[key]}")
                else:
                    print(f"key={key} not found in param_dict")

            # Update the row with dictionary values
            for key, value in next_point.items():
                if (
                    key in input_exp_df.columns
                ):  # Ensure the column exists in the DataFrame
                    input_exp_df.at[number_of_parameter, key] = value
                else:
                    print(f"key={key} not found in input_exp_df")

            if DEBUG:
                stop_bo_loop()
            else:
                # Updates the working excel file
                input_exp_df.to_excel(
                    experiments_input_path, index=False, engine="openpyxl"
                )

            # Saves a readable version of the Excel file to the Archive
            path_readable_excel_file = f"{archive_folder_path}\\optimize_{datetime.now().strftime('%H%M%S_%d%m%Y')}.xlsx"
            input_exp_df.to_excel(path_readable_excel_file, index=False)

        # Stops the Loop if the Excel Sheet is full or something unexpected happens
        else:
            print("Unexpected error occured")
            stop_bo_loop()

        if counter % 300 == 0:
            print("Loop is running...")
        counter += 1

        time.sleep(1)

    print("Loop has been stopped.")


def stop_bo_loop():
    global stop_loop
    stop_loop = True  # This will stop the loop
    print("stop loop is set true")


def start_bo_loop(session):
    loop_thread = threading.Thread(target=bayesian_optimization_loop, args=[session])
    loop_thread.start()

# station_id = 1

if __name__ == "__main__":
    if DEBUG:
        print("!" * 40)
        print("DEBUG MODE")
        print("!" * 40)
        
    start_bo_loop()
    time.sleep(10)
    stop_bo_loop()
