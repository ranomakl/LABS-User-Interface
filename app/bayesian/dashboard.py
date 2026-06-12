import streamlit as st
import pandas as pd
import numpy as np
import torch
import json
import plotly.graph_objects as go
import plotly.express as px
from optimizer import create_GP
import os

import seaborn as sns

# Import acquisition functions
from botorch.acquisition import (
    UpperConfidenceBound,
    LogExpectedImprovement,
    ProbabilityOfImprovement,
    qMaxValueEntropy,
)
from botorch.utils.sampling import draw_sobol_samples
from gpytorch.mlls import ExactMarginalLogLikelihood

# Get colors from seaborn pastel theme
# need A hex string (e.g. '#ff0000') - An rgb/rgba string (e.g. 'rgb(255,0,0)') - An hsl/hsla string (e.g. 'hsl(0,100%,50%)') - An hsv/hsva string (e.g. 'hsv(0,100%,100%)')
pastel_colors = sns.color_palette("muted").as_hex()


# Set page config
st.set_page_config(
    page_title="Bayesian Optimization Dashboard",
    # page_size="wide",
    layout="wide",
)


# Load configuration files
@st.cache_data
def load_config():
    """Load parameter and optimizer configuration"""
    with open("parameter.json", "r") as f:
        parameter_config = json.load(f)

    with open("optimizer_config.json", "r") as f:
        optimizer_config = json.load(f)

    return parameter_config, optimizer_config


@st.cache_data
def load_experiments_data():
    """Load and process the experiments data"""
    try:
        df = pd.read_excel("Experiments.xlsx", engine="openpyxl")

        # Filter out rows with null yields (incomplete experiments)
        df_completed = df.dropna(subset=["Yield"]).copy()

        # Parameter columns start from index 12
        param_columns = df.columns[12:].tolist()

        return df_completed, param_columns
    except Exception as e:
        st.error(f"Error loading Excel file: {e}")
        return None, None


def fit_gp_model(df, param_columns, parameter_config):
    """Fit the Gaussian Process model to the data"""
    # Filter for active parameters (not excluded from BO)
    active_param_columns = [
        col
        for col in param_columns
        if not parameter_config.get(col, {}).get("excluded_from_BO", False)
    ]
    
    # Debug: Check data types in active parameter columns and ensure they exist
    valid_active_param_columns = []
    for col in active_param_columns:
        if col in df.columns:
            # Check for non-numeric values
            non_numeric = df[col].apply(lambda x: not isinstance(x, (int, float, np.number)) and pd.notna(x))
            if non_numeric.any():
                problematic_rows = df[non_numeric].index.tolist()
                print(f"Warning: Non-numeric values in column '{col}' at rows: {problematic_rows}")
                print(f"Values: {df.loc[problematic_rows, col].tolist()}")
                # Try to convert problematic values to float, skip if impossible
                try:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                    print(f"Converted column '{col}' to numeric")
                except:
                    print(f"Skipping column '{col}' due to conversion issues")
                    continue
            valid_active_param_columns.append(col)
        else:
            print(f"Warning: Column '{col}' not found in dataframe. Available columns: {df.columns.tolist()}")
    
    active_param_columns = valid_active_param_columns
    
    if not active_param_columns:
        raise ValueError("No valid parameter columns found for Bayesian Optimization")

    # Extract parameter data and ensure it's numeric
    X = df[active_param_columns].values
    X = X.astype(float)
    X_tensor = torch.tensor(X, dtype=torch.double)

    # Extract yields
    y = df["Yield"].values.reshape(-1, 1)
    y = y.astype(float)
    y_tensor = torch.tensor(y, dtype=torch.double)

    # Extract bounds for active parameters
    param_bounds = []
    for col in active_param_columns:
        if (
            col in parameter_config
            and parameter_config[col].get("type") == "continuous"
        ):
            min_val = parameter_config[col]["min"]
            max_val = parameter_config[col]["max"]
            param_bounds.append([min_val, max_val])

    bounds = torch.tensor(param_bounds, dtype=torch.double).T  # shape: [2, d]

    # Fit GP model
    model, likelihood = create_GP(X_tensor, y_tensor, bounds=bounds)

    return model, likelihood, bounds, active_param_columns


def predict_gp(model, X_test):
    """Make predictions with the GP model"""
    model.eval()
    with torch.no_grad():
        posterior = model.posterior(X_test)
        mean = posterior.mean.squeeze(-1)
        std = posterior.variance.sqrt().squeeze(-1)
    return mean, std


def compute_acquisition_values(
    model, X_test, acquisition_function_name, bounds, observed_y, beta=2.0
):
    """Compute acquisition function values"""
    model.eval()

    if acquisition_function_name == "LogExpectedImprovement":
        best_f = observed_y.max()
        acq_func = LogExpectedImprovement(model, best_f=best_f, maximize=True)
    elif acquisition_function_name == "UpperConfidenceBound":
        acq_func = UpperConfidenceBound(model, beta=beta, maximize=True)
    elif acquisition_function_name == "ProbabilityOfImprovement":
        best_f = observed_y.max()
        acq_func = ProbabilityOfImprovement(model, best_f=best_f, maximize=True)
    elif acquisition_function_name == "MaxValueEntropy":
        # Generate candidate set for MaxValueEntropy
        candidate_set = draw_sobol_samples(bounds=bounds, n=1000, q=1).squeeze(1)
        acq_func = qMaxValueEntropy(model, candidate_set, maximize=True)
    else:
        raise ValueError(f"Unknown acquisition function: {acquisition_function_name}")

    with torch.no_grad():
        acq_values = acq_func(X_test.unsqueeze(1))  # Add batch dimension

    return acq_values.squeeze()


def main():
    st.title("🧪 Bayesian Optimization")
    st.markdown("""
**Abbreviations:**
- **DoE**: Design of Experiments
- **BO**: Bayesian Optimization
- **GP**: Gaussian Process
- **UCB**: Upper Confidence Bound (BO acquisition function)
- **EI**: Expected Improvement (BO acquisition function)
- **PI**: Probability of Improvement (BO acquisition function)
- **LEI**: Log Expected Improvement (BO acquisition function)
- **MaxEntropy**: Maximum Value Entropy (BO acquisition function)
- **MLL**: Marginal Log Likelihood
- **CDF**: Cumulative Distribution Function
- **PDF**: Probability Density Function
    """)

    # global settings
    # line 37 is the last of the DoE experiments
    # including the header row -> 36 DoE experiments
    start_idx_doe = 1
    # 38, 39, 40 are manual experiments with extended bounds
    #start_idx_manual_extended_bounds = 37
    # 41, 42, 43 are repeated experiments due to bugs
    #start_idx_repeated_experiments = 40
    # 44 to 50 are max entropy (exploration)
    #start_idx_bo = 43
    #start_idx_bo_exploration = 43
    # BO with Beta = 7 and beta decay = 3 %
    start_idx_ucb = 37
    start_idxs = [
        [start_idx_doe, "DoE", "top"],
        #[start_idx_manual_extended_bounds, "Manual", "top"],
        #[start_idx_repeated_experiments, "Repeated", "bottom"],
        # [start_idx_bo, "BO"],
        #[start_idx_bo_exploration, "MaxEntropy", "top"],
        [start_idx_ucb, "UCB", "bottom"],
    ]

    torch.manual_seed(0)
    np.random.seed(0)

    # Load data and configuration
    parameter_config, optimizer_config = load_config()
    df, param_columns = load_experiments_data()

    if df is None or df.empty:
        st.error("No data available. Please check the Experiments.xlsx file.")
        return

    st.success(f"Loaded {len(df)} completed experiments")

    # Fit GP model
    model, likelihood, bounds, active_param_columns = fit_gp_model(
        df, param_columns, parameter_config
    )
    # st.success("✅ Gaussian Process model fitted successfully")

    # set plotly theme
    px.defaults.template = "plotly_white"

    #####################################
    # Sidebar for parameter selection and controls
    #####################################
    st.sidebar.header("📊 Visualization Controls")

    # Select parameter to plot on X-axis
    selected_param = st.sidebar.selectbox(
        "Select parameter for X-axis:",
        active_param_columns,
        help="Choose which parameter to vary on the X-axis",
    )

    if selected_param not in active_param_columns:
        st.error(f"Selected parameter {selected_param} not found in active parameters")
        return

    # Get parameter info
    selected_param_info = parameter_config.get(selected_param, {})
    param_min = selected_param_info.get("min", 0)
    param_max = selected_param_info.get("max", 1)
    param_unit = selected_param_info.get("unit", "")

    st.sidebar.subheader("🎛️ Parameter Settings")
    st.sidebar.markdown(f"**{selected_param}** will be plotted on X-axis")
    st.sidebar.markdown(f"Range: {param_min} - {param_max} {param_unit}")

    # Create sliders for other parameters
    st.sidebar.subheader("🔧 Fix Other Parameters")
    fixed_params = {}

    for param in active_param_columns:
        if param != selected_param:
            param_info = parameter_config.get(param, {})
            param_min_val = param_info.get("min", 0)
            param_max_val = param_info.get("max", 1)
            param_unit_val = param_info.get("unit", "")

            # Default to max value as requested
            default_val = param_max_val

            fixed_params[param] = st.sidebar.slider(
                f"{param} {f'({param_unit_val})' if param_unit_val else ''}",
                min_value=float(param_min_val),
                max_value=float(param_max_val),
                value=float(default_val),
                step=(param_max_val - param_min_val) / 100,
                help=f"Fix {param} at this value",
            )

    # Select acquisition function
    acquisition_function_options = {
        "LogExpectedImprovement": "Log Expected Improvement",
        "UpperConfidenceBound": "Upper Confidence Bound",
        "MaxValueEntropy": "Max Value Entropy",
    }

    selected_acq_func = st.sidebar.selectbox(
        "Select acquisition function:",
        list(acquisition_function_options.keys()),
        format_func=lambda x: acquisition_function_options[x],
        help="Choose which acquisition function to plot",
    )

    if selected_acq_func == "UpperConfidenceBound":
        beta = st.sidebar.slider(
            "Beta",
            min_value=0.1,
            max_value=10.0,
            value=2.0,
            step=0.1,
            help="Beta value for UCB",
        )
    else:
        beta = None

    #####################################
    # Generate predictions
    #####################################
    st.header("Gaussian Process Predictions")

    # Create test points
    n_points = 100
    x_range = np.linspace(param_min, param_max, n_points)

    # Create test matrix
    test_matrix = []
    param_idx = active_param_columns.index(selected_param)

    for x_val in x_range:
        test_point = []
        for i, param in enumerate(active_param_columns):
            if param == selected_param:
                test_point.append(x_val)
            else:
                test_point.append(fixed_params[param])
        test_matrix.append(test_point)

    X_test = torch.tensor(test_matrix, dtype=torch.double)

    # Make predictions
    mean_pred, std_pred = predict_gp(model, X_test)

    # Convert to numpy for plotting
    mean_np = mean_pred.numpy()
    std_np = std_pred.numpy()

    # Create interactive plot of GP predictions
    fig = go.Figure()

    # Add confidence intervals
    fig.add_trace(
        go.Scatter(
            x=np.concatenate([x_range, x_range[::-1]]),
            y=np.concatenate([mean_np + 2 * std_np, (mean_np - 2 * std_np)[::-1]]),
            fill="toself",
            fillcolor="rgba(68, 114, 196, 0.2)",
            line=dict(color="rgba(255,255,255,0)"),
            name="95% Confidence Interval",
            showlegend=True,
        )
    )

    fig.add_trace(
        go.Scatter(
            x=np.concatenate([x_range, x_range[::-1]]),
            y=np.concatenate([mean_np + std_np, (mean_np - std_np)[::-1]]),
            fill="toself",
            fillcolor="rgba(68, 114, 196, 0.4)",
            line=dict(color="rgba(255,255,255,0)"),
            name="68% Confidence Interval",
            showlegend=True,
        )
    )

    # Add mean prediction
    fig.add_trace(
        go.Scatter(
            x=x_range,
            y=mean_np,
            mode="lines",
            # line=dict(width=3),
            line=dict(color=pastel_colors[0], width=3),
            name="GP Mean Prediction",
        )
    )

    # Add observed data points
    observed_x = df[selected_param].values
    observed_y = df["Yield"].values

    fig.add_trace(
        go.Scatter(
            x=observed_x,
            y=observed_y,
            mode="markers",
            # marker=dict(size=8, symbol='circle'),
            marker=dict(color=pastel_colors[1], size=8, symbol="circle"),
            name="Observed Data",
            text=[f"Exp {i + 1}: {y:.1f}%" for i, y in enumerate(observed_y)],
            hovertemplate="%{text}<br>%{x:.3f}<br>Yield: %{y:.1f}%<extra></extra>",
        )
    )

    # Update layout
    fig.update_layout(
        title=f"GP Prediction: Yield vs {selected_param}",
        xaxis_title=f"{selected_param} {f'({param_unit})' if param_unit else ''}",
        yaxis_title="Yield (%)",
        hovermode="closest",
        template="plotly_white",
        width=800,
        height=500,
        showlegend=True,
    )

    st.plotly_chart(fig, width='stretch')

    #####################################
    # Acquisition Function Plot
    #####################################
    st.header("Acquisition Function")

    # Compute acquisition function values
    observed_y_tensor = torch.tensor(df["Yield"].values, dtype=torch.double)
    acq_values = compute_acquisition_values(
        model, X_test, selected_acq_func, bounds, observed_y_tensor, beta=beta
    )
    acq_np = acq_values.numpy()

    # Create acquisition function plot
    fig_acq = go.Figure()

    # Add acquisition function values
    fig_acq.add_trace(
        go.Scatter(
            x=x_range,
            y=acq_np,
            mode="lines",
            line=dict(color=pastel_colors[2], width=3),
            name=f"{acquisition_function_options[selected_acq_func]}",
        )
    )

    # Find and mark the maximum acquisition value
    max_acq_idx = np.argmax(acq_np)
    max_acq_x = x_range[max_acq_idx]
    max_acq_y = acq_np[max_acq_idx]

    fig_acq.add_trace(
        go.Scatter(
            x=[max_acq_x],
            y=[max_acq_y],
            mode="markers",
            marker=dict(color=pastel_colors[3], size=12, symbol="star"),
            name="Max Acquisition",
            hovertemplate=f"Suggested next point<br>{selected_param}: %{{x:.3f}}<br>Acquisition: %{{y:.3f}}<extra></extra>",
        )
    )

    # Update layout
    fig_acq.update_layout(
        title=f"Acquisition Function: {acquisition_function_options[selected_acq_func]} vs {selected_param}",
        xaxis_title=f"{selected_param} {f'({param_unit})' if param_unit else ''}",
        yaxis_title="Acquisition Value",
        hovermode="closest",
        template="plotly_white",
        width=800,
        height=400,
        showlegend=True,
    )

    st.plotly_chart(fig_acq, width='stretch')

    # Show suggested next point
    st.markdown(
        f"**Suggested next `{selected_param}`, given that the other parameters are fixed**: {selected_param} = {max_acq_x:.3f} {param_unit}"
    )

    # Compute and show best global parameter set predicted by the GP (by mean)
    num_global_candidates = 5000
    global_candidates = draw_sobol_samples(
        bounds=bounds, n=num_global_candidates, q=1
    ).squeeze(1)

    global_mean, global_std = predict_gp(model, global_candidates)
    best_idx = int(torch.argmax(global_mean).item())
    best_x = global_candidates[best_idx]
    best_y = float(global_mean[best_idx].item())

    # Map to parameter names and format with units
    best_params_items = []
    for i, param in enumerate(active_param_columns):
        unit = parameter_config.get(param, {}).get("unit", "")
        unit_str = f" {unit}" if unit else ""
        best_params_items.append(f"{param} = {best_x[i].item():.3f}{unit_str}")

    st.markdown(
        "**Best global parameter set (GP mean, ignoring low confidence)**:  \n"
        + "  \n".join(best_params_items)
        + f"  \n**Predicted yield at best set**: {best_y:.1f}%"
    )


    # Show predicted optimum
    optimal_idx = np.argmax(mean_np)
    optimal_x = x_range[optimal_idx]
    optimal_y = mean_np[optimal_idx]
    optimal_std = std_np[optimal_idx]

    # st.subheader(f"🎯 Predicted Optimum of {selected_param} at fixed parameters")
    # st.success(f"**Optimal {selected_param}**: {optimal_x:.3f} {param_unit}")
    # st.success(f"**Predicted Yield**: {optimal_y:.1f}% ± {optimal_std:.1f}%")

    #####################################
    # Model training progress
    #####################################
    st.header("Model Training Progress")
    
    # Start from experiment 43 (index 42) as mentioned in comments
    start_idx = 42
    
    if len(df) > start_idx:
        # Initialize lists to store metrics
        experiment_numbers = []
        yields = []
        best_yields = []
        acquisition_values_lei = []
        acquisition_values_ucb0 = []
        acquisition_values_ucb2 = []
        acquisition_values_ucb5 = []
        acquisition_values_pi = []
        log_marginal_likelihoods = []
        # Predicted global best logs
        predicted_best_yields = []
        predicted_best_uncertainties = []
        predicted_best_params = []  # list of arrays aligned with current_active_params
        
        # Create yield plots starting from experiment 1 (all data)
        all_experiment_numbers = list(range(1, len(df) + 1))
        all_yields = df["Yield"].values.tolist()
        all_best_yields = df["Yield"].cummax().values.tolist()
        
        # Progressive training starting from experiment 43
        total_experiments = len(df) - start_idx
        
        # Initialize progress indicators
        st.info(f"🔄 Starting progressive training for {total_experiments} experiments (from experiment {start_idx + 1} to {len(df)})...")
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Progressive training loop
        import time
        start_time = time.time()
        
        for idx, i in enumerate(range(start_idx, len(df))):
            # Update progress bar and status
            progress = (idx + 1) / total_experiments
            progress_bar.progress(progress)
            status_text.text(f"Processing experiment {i+1}/{len(df)} ({progress:.1%} complete)")
            
            # Get data up to current experiment
            current_df = df.iloc[:i+1].copy()
            
            # Fit GP model with current data
            current_model, current_likelihood, current_bounds, current_active_params = fit_gp_model(
                current_df, param_columns, parameter_config
            )
            
            # Store experiment number (1-indexed)
            experiment_numbers.append(i + 1)
            
            # Store current yield
            current_yield = df.iloc[i]["Yield"]
            yields.append(current_yield)
            
            # Store best yield so far
            best_yield = current_df["Yield"].max()
            best_yields.append(best_yield)
            
            # Calculate acquisition value for current experiment point
            if i > start_idx:  # Skip first point as it doesn't have acquisition value
                # Get the current experiment's parameter values
                current_params = df.iloc[i][current_active_params].values
                current_params = current_params.astype(float)
                current_X = torch.tensor(current_params, dtype=torch.double).unsqueeze(0)
                
                # Use previous model (trained without current point) to compute acquisition
                prev_df = df.iloc[:i].copy()
                prev_model, prev_likelihood, prev_bounds, prev_active_params = fit_gp_model(
                    prev_df, param_columns, parameter_config
                )
                
                # Compute acquisition values for multiple functions
                prev_y_tensor = torch.tensor(prev_df["Yield"].values, dtype=torch.double)
                
                # Log Expected Improvement
                acq_val_lei = compute_acquisition_values(
                    prev_model, current_X, "LogExpectedImprovement", prev_bounds, prev_y_tensor
                )
                acquisition_values_lei.append(float(acq_val_lei.item()))
                
                # UCB with beta=0
                acq_val_ucb0 = compute_acquisition_values(
                    prev_model, current_X, "UpperConfidenceBound", prev_bounds, prev_y_tensor, beta=0.0
                )
                acquisition_values_ucb0.append(float(acq_val_ucb0.item()))
                
                # UCB with beta=2
                acq_val_ucb2 = compute_acquisition_values(
                    prev_model, current_X, "UpperConfidenceBound", prev_bounds, prev_y_tensor, beta=2.0
                )
                acquisition_values_ucb2.append(float(acq_val_ucb2.item()))
                
                # UCB with beta=5
                acq_val_ucb5 = compute_acquisition_values(
                    prev_model, current_X, "UpperConfidenceBound", prev_bounds, prev_y_tensor, beta=5.0
                )
                acquisition_values_ucb5.append(float(acq_val_ucb5.item()))
                
                # Probability of Improvement
                acq_val_pi = compute_acquisition_values(
                    prev_model, current_X, "ProbabilityOfImprovement", prev_bounds, prev_y_tensor
                )
                acquisition_values_pi.append(float(acq_val_pi.item()))
            else:
                # No acquisition for first point
                acquisition_values_lei.append(0.0)
                acquisition_values_ucb0.append(0.0)
                acquisition_values_ucb2.append(0.0)
                acquisition_values_ucb5.append(0.0)
                acquisition_values_pi.append(0.0)
            
            # Calculate log marginal likelihood
            current_model.eval()
            current_likelihood.eval()
            mll = ExactMarginalLogLikelihood(current_likelihood, current_model)
            
            # Get training data
            X_train = current_df[current_active_params].values
            X_train = X_train.astype(float)
            X_tensor = torch.tensor(X_train, dtype=torch.double)
            y_train = current_df["Yield"].values.reshape(-1, 1)
            y_train = y_train.astype(float)
            y_tensor = torch.tensor(y_train, dtype=torch.double)
            with torch.no_grad():
                log_mll = mll(current_model(X_tensor), y_tensor.squeeze()).item()
            log_marginal_likelihoods.append(log_mll)
            
            # Log predicted global best (by GP mean) for current model
            try:
                num_candidates_progress = 2000
                candidate_set_progress = draw_sobol_samples(
                    bounds=current_bounds, n=num_candidates_progress, q=1
                ).squeeze(1)
                mean_progress, std_progress = predict_gp(current_model, candidate_set_progress)
                best_idx_progress = int(torch.argmax(mean_progress).item())
                best_x_progress = candidate_set_progress[best_idx_progress].cpu().numpy()
                best_mean_progress = float(mean_progress[best_idx_progress].item())
                best_std_progress = float(std_progress[best_idx_progress].item())
                predicted_best_yields.append(best_mean_progress)
                predicted_best_uncertainties.append(best_std_progress)
                predicted_best_params.append(best_x_progress)
            except Exception as e:
                predicted_best_yields.append(float('nan'))
                predicted_best_uncertainties.append(float('nan'))
                predicted_best_params.append([float('nan')] * len(current_active_params))
        
        # Clean up progress indicators
        progress_bar.progress(1.0)
        status_text.text("✅ Progressive training completed successfully!")
        
        # Show success message and clean up
        end_time = time.time()
        elapsed_time = end_time - start_time
        time.sleep(0.5)  # Brief pause to show completion
        progress_bar.empty()
        status_text.empty()
        
        st.success(f"Progressive training completed in {elapsed_time:.1f} seconds! Processed {total_experiments} experiments with multiple acquisition functions.")
        
        if experiment_numbers:
            # Create subplot layout
            col1, col2 = st.columns(2)
            
            with col1:
                #########################################################
                # Plot Yield over experiment number (all experiments)
                fig_yield = go.Figure()
                fig_yield.add_trace(
                    go.Scatter(
                        x=all_experiment_numbers,
                        y=all_yields,
                        mode="lines+markers",
                        line=dict(color=pastel_colors[0], width=2),
                        marker=dict(size=6),
                        name="Yield",
                        hovertemplate="Experiment %{x}<br>Yield: %{y:.1f}%<extra></extra>"
                    )
                )
                
                # Add vertical lines
                for idx, name, pos in start_idxs:
                    fig_yield.add_vline(
                        x=idx, 
                        line_dash="dash", 
                        line_color="red",
                        annotation_text=name,
                        annotation_position=pos
                    )
                
                fig_yield.update_layout(
                    title="Yield vs Experiment Number",
                    xaxis_title="Experiment Number",
                    yaxis_title="Yield (%)",
                    template="plotly_white",
                    height=400
                )
                # Ensure x-axis starts at 0
                fig_yield.update_xaxes(range=[0, len(all_experiment_numbers)])
                st.plotly_chart(fig_yield, width='stretch')
                
                #########################################################
                # Plot UCB variants
                fig_ucb = go.Figure()
                
                # UCB beta=0
                fig_ucb.add_trace(
                    go.Scatter(
                        x=experiment_numbers[1:],
                        y=acquisition_values_ucb0[1:],
                        mode="lines+markers",
                        line=dict(color=pastel_colors[1], width=2),
                        marker=dict(size=6),
                        name="UCB β=0",
                        hovertemplate="Experiment %{x}<br>UCB β=0: %{y:.3f}<extra></extra>"
                    )
                )
                
                # UCB beta=2
                fig_ucb.add_trace(
                    go.Scatter(
                        x=experiment_numbers[1:],
                        y=acquisition_values_ucb2[1:],
                        mode="lines+markers",
                        line=dict(color=pastel_colors[2], width=2),
                        marker=dict(size=6),
                        name="UCB β=2",
                        hovertemplate="Experiment %{x}<br>UCB β=2: %{y:.3f}<extra></extra>"
                    )
                )
                
                # UCB beta=5
                fig_ucb.add_trace(
                    go.Scatter(
                        x=experiment_numbers[1:],
                        y=acquisition_values_ucb5[1:],
                        mode="lines+markers",
                        line=dict(color=pastel_colors[3], width=2),
                        marker=dict(size=6),
                        name="UCB β=5",
                        hovertemplate="Experiment %{x}<br>UCB β=5: %{y:.3f}<extra></extra>"
                    )
                )
                
                fig_ucb.update_layout(
                    title="Upper Confidence Bound (UCB) Variants",
                    xaxis_title="Experiment Number",
                    yaxis_title="UCB Value",
                    template="plotly_white",
                    height=400,
                )
                fig_ucb.update_xaxes(range=[np.min(experiment_numbers[1:]), np.max(experiment_numbers[1:])])
                st.plotly_chart(fig_ucb, width='stretch')

                #########################################################
                # Plot Expected Improvement (converted from Log EI)
                fig_lei = go.Figure()
                fig_lei.add_trace(
                    go.Scatter(
                        x=experiment_numbers[1:],
                        y=np.exp(acquisition_values_lei[1:]),
                        mode="lines+markers",
                        line=dict(color=pastel_colors[0], width=2),
                        marker=dict(size=6),
                        name="EI",
                        hovertemplate="Experiment %{x}<br>EI: %{y:.3f}<extra></extra>"
                    )
                )
                # Add vertical lines
                for idx, name, pos in start_idxs:
                    fig_lei.add_vline(
                        x=idx, 
                        line_dash="dash", 
                        line_color="red",
                        annotation_text=name,
                        annotation_position=pos
                    )
                fig_lei.update_layout(
                    title="Expected Improvement (EI) (exp(Log EI))",
                    xaxis_title="Experiment Number",
                    yaxis_title="Expected Improvement",
                    template="plotly_white",
                    height=400
                )
                fig_lei.update_xaxes(range=[np.min(experiment_numbers[1:]), np.max(experiment_numbers[1:])])
                st.plotly_chart(fig_lei, width='stretch')
                st.caption(
                    "EI is on the objective's units (here: yield percentage points). "
                    "An EI of 1 means the model's posterior predicts, on average, a 1 percentage-point improvement over the current best yield. "
                    "Formula: `EI(x)=E[I(x)]=E[max(0,f(x)-f*)]`, where `f*` is the current best yield. " 
                    "Since we are using a GP: `EI(x)=[mean(x)-f*]*CDF(Z)+sigma(x)*PDF(Z)`, where `Z=(mean(x)-f*)/sigma(x)` is the standard normal variable."
                )
            
            with col2:
                #########################################################
                # Plot Best yield over experiment number (all experiments)
                fig_best = go.Figure()
                fig_best.add_trace(
                    go.Scatter(
                        x=all_experiment_numbers,
                        y=all_best_yields,
                        mode="lines+markers",
                        line=dict(color=pastel_colors[1], width=3),
                        marker=dict(size=6),
                        name="Best Yield",
                        fill="tonexty",
                        hovertemplate="Experiment %{x}<br>Best Yield: %{y:.1f}%<extra></extra>"
                    )
                )
                # Add vertical lines
                for idx, name, pos in start_idxs:
                    fig_best.add_vline(
                        x=idx, 
                        line_dash="dash", 
                        line_color="red",
                        annotation_text=name,
                        annotation_position=pos
                    )
                fig_best.update_layout(
                    title="Best Yield Over Time",
                    xaxis_title="Experiment Number",
                    yaxis_title="Best Yield (%)",
                    template="plotly_white",
                    height=400
                )
                fig_best.update_xaxes(range=[np.min(all_experiment_numbers), np.max(all_experiment_numbers)])
                st.plotly_chart(fig_best, width='stretch')
                
                #########################################################
                # Plot Log marginal likelihood over experiment number
                fig_mll = go.Figure()
                fig_mll.add_trace(
                    go.Scatter(
                        x=experiment_numbers[1:],
                        y=log_marginal_likelihoods[1:],
                        mode="lines+markers",
                        line=dict(color=pastel_colors[5], width=2),
                        marker=dict(size=6),
                        name="Log Marginal Likelihood",
                        hovertemplate="Experiment %{x}<br>Log MLL: %{y:.2f}<extra></extra>"
                    )
                )
                fig_mll.update_layout(
                    title="Model Log Marginal Likelihood",
                    xaxis_title="Experiment Number",
                    yaxis_title="Log Marginal Likelihood",
                    template="plotly_white",
                    height=400
                )
                fig_mll.update_xaxes(range=[np.min(experiment_numbers[1:]), np.max(experiment_numbers[1:])])
                st.plotly_chart(fig_mll, width='stretch')

                #########################################################
                # Plot Probability of Improvement
                fig_pi = go.Figure()
                fig_pi.add_trace(
                    go.Scatter(
                        x=experiment_numbers[1:],
                        y=acquisition_values_pi[1:],
                        mode="lines+markers",
                        line=dict(color=pastel_colors[4], width=2),
                        marker=dict(size=6),
                        name="PI",
                        hovertemplate="Experiment %{x}<br>PI: %{y:.3f}<extra></extra>"
                    )
                )
                # Add vertical lines
                for idx, name, pos in start_idxs:
                    fig_pi.add_vline(
                        x=idx, 
                        line_dash="dash", 
                        line_color="red",
                        annotation_text=name,
                        annotation_position=pos
                    )
                fig_pi.update_layout(
                    title="Probability of Improvement",
                    xaxis_title="Experiment Number",
                    yaxis_title="Probability of Improvement",
                    template="plotly_white",
                    height=400
                )
                fig_pi.update_xaxes(range=[np.min(experiment_numbers[1:]), np.max(experiment_numbers[1:])])
                st.plotly_chart(fig_pi, width='stretch')
            
            #########################################################
            # Plot: Predicted best yield with uncertainty over training
            pred_best_mean = np.array(predicted_best_yields, dtype=float)
            pred_best_std = np.array(predicted_best_uncertainties, dtype=float)
            x_pred = np.array(experiment_numbers, dtype=float)
            
            fig_pred_best = go.Figure()
            # Confidence band (±1 std)
            fig_pred_best.add_trace(
                go.Scatter(
                    x=np.concatenate([x_pred, x_pred[::-1]]),
                    y=np.concatenate([pred_best_mean + pred_best_std, (pred_best_mean - pred_best_std)[::-1]]),
                    fill="toself",
                    fillcolor="rgba(68, 114, 196, 0.2)",
                    line=dict(color="rgba(255,255,255,0)"),
                    name="±1σ",
                    showlegend=True,
                )
            )
            # Mean line
            fig_pred_best.add_trace(
                go.Scatter(
                    x=x_pred,
                    y=pred_best_mean,
                    mode="lines+markers",
                    line=dict(color=pastel_colors[0], width=3),
                    marker=dict(size=6),
                    name="Predicted Best Yield",
                    hovertemplate="Experiment %{x}<br>Predicted Best: %{y:.1f}%<extra></extra>"
                )
            )
            # add a horizontal line at the current best yield
            fig_pred_best.add_hline(
                y=max(all_best_yields),
                line_dash="dash",
                line_color="red",
                annotation_text="Best Yield",
                annotation_position="bottom right",
            )
            fig_pred_best.update_layout(
                title="Predicted Best Possible Yield (GP mean) with Uncertainty",
                xaxis_title="Experiment Number",
                yaxis_title="Yield (%)",
                template="plotly_white",
                height=400
            )
            fig_pred_best.update_xaxes(range=[np.min(experiment_numbers), np.max(experiment_numbers)])
            st.plotly_chart(fig_pred_best, width='stretch')
            
            # Table: Predicted global best over time
            with st.expander("Predicted Global Best Over Time"):
                df_pred_best = pd.DataFrame({
                    "Experiment": experiment_numbers,
                    "Predicted Best Yield (%)": predicted_best_yields,
                    "Predicted Uncertainty (%)": predicted_best_uncertainties,
                })
                try:
                    params_matrix = np.vstack(predicted_best_params)
                    params_df = pd.DataFrame(params_matrix, columns=current_active_params)
                    df_pred_best = pd.concat([df_pred_best, params_df], axis=1)
                except Exception:
                    pass
                st.dataframe(df_pred_best, width='stretch')
            
            # Summary statistics
            st.subheader("📊 Optimization Summary")
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric(
                    "Best Yield Achieved", 
                    f"{max(all_best_yields):.1f}%",
                    delta=f"+{max(all_best_yields) - all_best_yields[0]:.1f}%" if len(all_best_yields) > 1 else None
                )
            
            with col2:
                # Calculate improvement rate for BO phase only
                if len(best_yields) > 1:
                    bo_improvement_rate = (best_yields[-1] - best_yields[0]) / len(best_yields)
                    st.metric(
                        "BO Improvement Rate", 
                        f"{bo_improvement_rate:.2f}%/exp"
                    )
                else:
                    st.metric("BO Improvement Rate", "N/A")
            
            with col3:
                if len(acquisition_values_lei) > 1:
                    avg_acquisition_lei = np.mean(acquisition_values_lei[1:])
                    st.metric(
                        "Avg Log EI", 
                        f"{avg_acquisition_lei:.3f}"
                    )
            
            with col4:
                if log_marginal_likelihoods:
                    final_mll = log_marginal_likelihoods[-1]
                    st.metric(
                        "Final Log MLL", 
                        f"{final_mll:.2f}"
                    )
            
            # Show optimization insights
            with st.expander("🔍 Optimization Insights"):
                st.markdown("### Key Findings:")
                
                # Find best experiment (across all experiments)
                best_exp_idx = np.argmax(all_yields)
                best_exp_num = all_experiment_numbers[best_exp_idx]
                st.markdown(f"- **Best performing experiment**: #{best_exp_num} with {all_yields[best_exp_idx]:.1f}% yield")
                
                # Calculate overall improvement
                if len(all_yields) > 1:
                    total_improvement = max(all_yields) - all_yields[0]
                    st.markdown(f"- **Total improvement**: +{total_improvement:.1f}% yield over {len(all_yields)} experiments")
                    
                # Calculate improvement during BO phase
                if len(yields) > 1:
                    bo_improvement = max(yields) - yields[0]
                    st.markdown(f"- **BO phase improvement**: +{bo_improvement:.1f}% yield over {len(yields)} BO experiments (exp 43+)")
                
                # Acquisition function insights
                if len(acquisition_values_lei) > 1:
                    high_acq_experiments_lei = [i for i, val in enumerate(acquisition_values_lei[1:], start=1) if val > np.median(acquisition_values_lei[1:])]
                    st.markdown(f"- **High Log EI experiments**: {len(high_acq_experiments_lei)} experiments had above-median Log EI values")
                    
                    # Compare acquisition functions
                    acq_correlations = []
                    if len(acquisition_values_ucb2) > 1:
                        corr_lei_ucb2 = np.corrcoef(acquisition_values_lei[1:], acquisition_values_ucb2[1:])[0, 1]
                        acq_correlations.append(f"Log EI vs UCB β=2: {corr_lei_ucb2:.3f}")
                    
                    if len(acquisition_values_pi) > 1:
                        corr_lei_pi = np.corrcoef(acquisition_values_lei[1:], acquisition_values_pi[1:])[0, 1]
                        acq_correlations.append(f"Log EI vs PI: {corr_lei_pi:.3f}")
                    
                    if acq_correlations:
                        st.markdown(f"- **Acquisition function correlations**: {', '.join(acq_correlations)}")
                
                # Model confidence trend
                if len(log_marginal_likelihoods) > 5:
                    recent_mll = np.mean(log_marginal_likelihoods[-5:])
                    early_mll = np.mean(log_marginal_likelihoods[:5])
                    if recent_mll > early_mll:
                        st.markdown("- **Model quality**: Log marginal likelihood improved over time (better model fit)")
                    else:
                        st.markdown("- **Model quality**: Log marginal likelihood decreased over time (potential overfitting)")
    
    else:
        st.info(f"Not enough experiments for training progress analysis. Need at least {start_idx + 1} experiments.")
        st.markdown(f"Current experiments: {len(df)}")


    #####################################
    # View raw data
    #####################################
    # Data overview
    with st.expander("📋 View Raw Data"):
        st.dataframe(df[["Yield"] + active_param_columns], width='stretch')


if __name__ == "__main__":
    main()
