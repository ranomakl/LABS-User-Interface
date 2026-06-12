from __future__ import annotations

import argparse
import itertools
import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(".matplotlib-cache").resolve()))
os.environ.setdefault("JOBLIB_TEMP_FOLDER", str(Path(".joblib-cache").resolve()))
warnings.filterwarnings("ignore", category=FutureWarning, module="shap")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import shap
import torch
from botorch.models.transforms import Normalize, Standardize
from botorch.models import SingleTaskGP
from gpytorch.mlls import ExactMarginalLogLikelihood
from botorch.fit import fit_gpytorch_mll
from scipy.stats import spearmanr
from sklearn.cross_decomposition import PLSRegression
from sklearn.decomposition import PCA
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.inspection import partial_dependence, permutation_importance, PartialDependenceDisplay
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import KFold, LeaveOneOut, cross_val_predict
from sklearn.preprocessing import StandardScaler


RANDOM_STATE = 11
TARGET = "Yield"
DESCRIPTOR_LABELS = {
    "current": "Current (A)",
    "applied_charge": "Applied charge (F)",
    "flowrate": "Flow rate (mL min$^{-1}$)",
    "DMSO_equivalents": "DMSO equivalents (equiv)",
    "SE_concentration": "Supporting electrolyte concentration (mol L$^{-1}$)",
    "nitro_concentration": "Nitroarene concentration (mol L$^{-1}$)",
    "volume": "Reaction volume (mL)",
    "residence_time_proxy_min": "Residence-time proxy (min)",
    "charge_per_current_proxy": "Charge/current proxy (F A$^{-1}$)",
    "charge_concentration_product": "Charge-concentration product (F mol L$^{-1}$)",
    "DMSO_to_nitro_ratio": "DMSO/nitroarene ratio",
    TARGET: "Yield (%)",
}


def display_label(name: str) -> str:
    return DESCRIPTOR_LABELS.get(name, name.replace("_", " ").title())


def display_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={column: display_label(column) for column in df.columns})


def shap_summary_label(name: str) -> str:
    labels = {
        "nitro_concentration": "Nitroarene con. (mol L$^{-1}$)",
        "SE_concentration": "Electrolyte con. (mol L$^{-1}$)",
    }
    return labels.get(name, display_label(name))


def shap_summary_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={column: shap_summary_label(column) for column in df.columns})


def clear_plot_titles(fig: plt.Figure) -> None:
    for ax in fig.axes:
        ax.set_title("")


@dataclass(frozen=True)
class ModelPerformance:
    loo_r2: float
    loo_mae: float
    kfold_r2: float
    kfold_mae: float


def read_parameter_columns(config_path: Path, df: pd.DataFrame) -> tuple[list[str], dict]:
    with config_path.open("r") as f:
        parameter_config = json.load(f)

    columns = []
    for column in df.columns:
        cfg = parameter_config.get(column)
        if not cfg:
            continue
        excluded = cfg.get("excluded_from_BO", cfg.get("excludet_from_BO", False))
        if cfg.get("type") == "continuous" and not excluded:
            columns.append(column)
    return columns, parameter_config


def add_derived_descriptors(df: pd.DataFrame) -> pd.DataFrame:
    derived = df.copy()
    derived["residence_time_proxy_min"] = derived["volume"] / derived["flowrate"]
    derived["charge_per_current_proxy"] = derived["applied_charge"] / derived["current"]
    derived["charge_concentration_product"] = (
        derived["applied_charge"] * derived["nitro_concentration"]
    )
    derived["DMSO_to_nitro_ratio"] = (
        derived["DMSO_equivalents"] / derived["nitro_concentration"]
    )
    return derived


def fit_tree_model(X: pd.DataFrame, y: pd.Series) -> RandomForestRegressor:
    model = RandomForestRegressor(
        n_estimators=1000,
        min_samples_leaf=3,
        max_features="sqrt",
        bootstrap=True,
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    model.fit(X, y)
    return model


def cross_validated_performance(X: pd.DataFrame, y: pd.Series) -> ModelPerformance:
    base_model = ExtraTreesRegressor(
        n_estimators=800,
        min_samples_leaf=3,
        max_features=1.0,
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    loo_pred = cross_val_predict(base_model, X, y, cv=LeaveOneOut(), n_jobs=1)
    kfold_pred = cross_val_predict(
        base_model,
        X,
        y,
        cv=KFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE),
        n_jobs=1,
    )
    return ModelPerformance(
        loo_r2=float(r2_score(y, loo_pred)),
        loo_mae=float(mean_absolute_error(y, loo_pred)),
        kfold_r2=float(r2_score(y, kfold_pred)),
        kfold_mae=float(mean_absolute_error(y, kfold_pred)),
    )


def gp_lengthscale_importance(
    X: pd.DataFrame, y: pd.Series, parameter_config: dict
) -> pd.DataFrame:
    bounds = torch.tensor(
        [[parameter_config[col]["min"], parameter_config[col]["max"]] for col in X.columns],
        dtype=torch.double,
    ).T
    train_x = torch.tensor(X.to_numpy(dtype=float), dtype=torch.double)
    train_y = torch.tensor(y.to_numpy(dtype=float).reshape(-1, 1), dtype=torch.double)
    model = SingleTaskGP(
        train_x,
        train_y,
        input_transform=Normalize(d=X.shape[1], bounds=bounds),
        outcome_transform=Standardize(m=1),
    )
    mll = ExactMarginalLogLikelihood(model.likelihood, model)
    fit_gpytorch_mll(mll)
    lengthscales = (
        model.covar_module.lengthscale.detach().cpu().numpy().reshape(-1).astype(float)
    )
    importance = 1 / lengthscales
    importance = importance / importance.sum()
    return pd.DataFrame(
        {
            "descriptor": X.columns,
            "gp_lengthscale_normalized_space": lengthscales,
            "gp_inverse_lengthscale_importance": importance,
        }
    ).sort_values("gp_inverse_lengthscale_importance", ascending=False)


def save_observed_progress(df: pd.DataFrame, out_dir: Path) -> None:
    completed = df.reset_index(drop=True).copy()
    completed["experiment_number"] = np.arange(1, len(completed) + 1)
    completed["best_yield_so_far"] = completed[TARGET].cummax()

    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.scatter(
        completed["experiment_number"],
        completed[TARGET],
        s=38,
        color="#5B8C85",
        label="Observed yield",
    )
    ax.plot(
        completed["experiment_number"],
        completed["best_yield_so_far"],
        color="#1F3A5F",
        linewidth=2.4,
        label="Best yield so far",
    )
    ax.axvline(36.1, color="#8C6D31", linestyle="--", linewidth=1.4, label="DoE/BO boundary")
    ax.set_xlabel("Experiment number")
    ax.set_ylabel("Yield (%)")
    ax.legend(frameon=False)
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "01_yield_progress.png", dpi=300)
    plt.close(fig)


def save_correlation_heatmap(X: pd.DataFrame, y: pd.Series, out_dir: Path) -> pd.DataFrame:
    rows = []
    for col in X.columns:
        rho, pvalue = spearmanr(X[col], y)
        rows.append({"descriptor": col, "spearman_rho": rho, "p_value": pvalue})
    corr_df = pd.DataFrame(rows).sort_values(
        "spearman_rho", key=lambda s: s.abs(), ascending=False
    )
    corr_df.to_csv(out_dir / "spearman_descriptor_yield.csv", index=False)

    combined = X.copy()
    combined[TARGET] = y
    corr = combined.corr(method="spearman")
    corr_display = corr.rename(index=display_label, columns=display_label)
    fig, ax = plt.subplots(figsize=(11.2, 9.4))
    sns.heatmap(
        corr_display,
        cmap="vlag",
        vmin=-1,
        vmax=1,
        center=0,
        square=True,
        linewidths=0.5,
        cbar_kws={"label": "Spearman rho"},
        ax=ax,
    )
    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right")
    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "02_descriptor_correlation_heatmap.png", dpi=300)
    plt.close(fig)
    return corr_df


def save_shap_outputs(
    model: RandomForestRegressor, X: pd.DataFrame, out_dir: Path
) -> pd.DataFrame:
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    shap_abs = np.abs(shap_values).mean(axis=0)
    shap_df = pd.DataFrame(
        {
            "descriptor": X.columns,
            "mean_abs_shap_yield_points": shap_abs,
        }
    ).sort_values("mean_abs_shap_yield_points", ascending=False)
    shap_df["relative_importance"] = (
        shap_df["mean_abs_shap_yield_points"]
        / shap_df["mean_abs_shap_yield_points"].sum()
    )
    shap_df.to_csv(out_dir / "shap_descriptor_importance.csv", index=False)

    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    plot_df = shap_df.sort_values("mean_abs_shap_yield_points")
    ax.barh(
        plot_df["descriptor"].map(display_label),
        plot_df["mean_abs_shap_yield_points"],
        color="#7E9F90",
    )
    ax.set_xlabel("Mean absolute SHAP value (yield percentage points)")
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "03_shap_global_importance.png", dpi=300)
    plt.close(fig)

    plt.figure(figsize=(7.4, 5.6))
    X_display = shap_summary_columns(X)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=FutureWarning)
        shap.summary_plot(shap_values, X_display, show=False, plot_size=None, color_bar=True)
    ax = plt.gca()
    ax.tick_params(axis="y", labelsize=10)
    clear_plot_titles(plt.gcf())
    plt.tight_layout(pad=0.01)
    plt.savefig(out_dir / "04_shap_directional_summary.png", dpi=300, bbox_inches="tight")
    plt.close()

    return shap_df


def save_dimension_reduction_plots(X: pd.DataFrame, y: pd.Series, out_dir: Path) -> None:
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    pca = PCA(n_components=2, random_state=RANDOM_STATE)
    pca_scores = pca.fit_transform(X_scaled)
    pca_scores_df = pd.DataFrame(
        {
            "PC1": pca_scores[:, 0],
            "PC2": pca_scores[:, 1],
            TARGET: y.to_numpy(dtype=float),
        }
    )
    pca_scores_df.to_csv(out_dir / "pca_descriptor_scores.csv", index=False)

    fig, ax = plt.subplots(figsize=(6.8, 5.6))
    scatter = ax.scatter(
        pca_scores_df["PC1"],
        pca_scores_df["PC2"],
        c=pca_scores_df[TARGET],
        cmap="viridis",
        s=48,
        edgecolor="#222222",
        linewidth=0.35,
    )
    ax.axhline(0, color="#7A7A7A", linewidth=0.8, linestyle="--")
    ax.axvline(0, color="#7A7A7A", linewidth=0.8, linestyle="--")
    ax.set_xlabel(f"PC1 ({100 * pca.explained_variance_ratio_[0]:.1f}% variance)")
    ax.set_ylabel(f"PC2 ({100 * pca.explained_variance_ratio_[1]:.1f}% variance)")
    clear_plot_titles(fig)
    fig.colorbar(scatter, ax=ax, label="Yield (%)")
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "11_pca_descriptor_score_yield.png", dpi=300)
    plt.close(fig)

    pls = PLSRegression(n_components=2, scale=False)
    pls_scores = pls.fit_transform(X_scaled, y.to_numpy(dtype=float).reshape(-1, 1))[0]
    pls_scores_df = pd.DataFrame(
        {
            "PLS1": pls_scores[:, 0],
            "PLS2": pls_scores[:, 1],
            TARGET: y.to_numpy(dtype=float),
        }
    )
    pls_scores_df.to_csv(out_dir / "pls_descriptor_scores.csv", index=False)

    fig, ax = plt.subplots(figsize=(6.8, 5.6))
    scatter = ax.scatter(
        pls_scores_df["PLS1"],
        pls_scores_df["PLS2"],
        c=pls_scores_df[TARGET],
        cmap="viridis",
        s=48,
        edgecolor="#222222",
        linewidth=0.35,
    )
    ax.axhline(0, color="#7A7A7A", linewidth=0.8, linestyle="--")
    ax.axvline(0, color="#7A7A7A", linewidth=0.8, linestyle="--")
    ax.set_xlabel("PLS component 1")
    ax.set_ylabel("PLS component 2")
    clear_plot_titles(fig)
    fig.colorbar(scatter, ax=ax, label="Yield (%)")
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "12_pls_descriptor_score_yield.png", dpi=300)
    plt.close(fig)


def save_shap_dependence_plots(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    shap_df: pd.DataFrame,
    out_dir: Path,
) -> None:
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    requested_features = [
        "current",
        "applied_charge",
        "DMSO_equivalents",
        "nitro_concentration",
    ]
    features = [feature for feature in requested_features if feature in X.columns]
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 7.0))
    axes = axes.ravel()
    top_feature = shap_df["descriptor"].iloc[0]
    X_display = display_columns(X)
    for ax, feature in zip(axes, features):
        interaction_feature = top_feature if feature != top_feature else shap_df["descriptor"].iloc[1]
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=FutureWarning)
            shap.dependence_plot(
                display_label(feature),
                shap_values,
                X_display,
                interaction_index=display_label(interaction_feature),
                ax=ax,
                show=False,
                dot_size=38,
            )
        ax.set_ylabel("SHAP value (yield points)")
    for ax in axes[len(features) :]:
        ax.axis("off")
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "13_shap_dependence_top_descriptors.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def save_permutation_importance(
    model: RandomForestRegressor, X: pd.DataFrame, y: pd.Series, out_dir: Path
) -> pd.DataFrame:
    result = permutation_importance(
        model,
        X,
        y,
        scoring="neg_mean_absolute_error",
        n_repeats=100,
        random_state=RANDOM_STATE,
        n_jobs=1,
    )
    perm_df = pd.DataFrame(
        {
            "descriptor": X.columns,
            "mae_increase_mean": result.importances_mean,
            "mae_increase_std": result.importances_std,
        }
    ).sort_values("mae_increase_mean", ascending=False)
    perm_df.to_csv(out_dir / "permutation_descriptor_importance.csv", index=False)

    fig, ax = plt.subplots(figsize=(7.2, 4.8))
    plot_df = perm_df.sort_values("mae_increase_mean")
    ax.barh(
        plot_df["descriptor"].map(display_label),
        plot_df["mae_increase_mean"],
        xerr=plot_df["mae_increase_std"],
        color="#C17C5A",
        alpha=0.9,
    )
    ax.set_xlabel("Increase in MAE after descriptor permutation")
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "05_permutation_importance.png", dpi=300)
    plt.close(fig)
    return perm_df


def save_response_plots(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    y: pd.Series,
    shap_df: pd.DataFrame,
    out_dir: Path,
) -> None:
    top_features = shap_df["descriptor"].head(4).tolist()
    fig, axes = plt.subplots(2, 2, figsize=(9.2, 7.0), sharey=False)
    axes = axes.ravel()
    for ax, col in zip(axes, top_features):
        sns.scatterplot(x=X[col], y=y, ax=ax, color="#4E7A99", s=40)
        sns.regplot(
            x=X[col],
            y=y,
            ax=ax,
            scatter=False,
            order=2,
            ci=None,
            color="#A84C3D",
            line_kws={"linewidth": 2},
        )
        ax.set_xlabel(display_label(col))
        ax.set_ylabel("Yield (%)")
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "06_observed_descriptor_trends.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    pdp_features = shap_df["descriptor"].head(3).tolist()
    fig, ax = plt.subplots(figsize=(9.2, 3.8))
    display = PartialDependenceDisplay.from_estimator(
        model,
        X,
        features=pdp_features,
        kind="average",
        grid_resolution=50,
        ax=ax,
    )
    for plot_ax, feature in zip(np.ravel(display.axes_), pdp_features):
        if plot_ax is None:
            continue
        plot_ax.set_xlabel(display_label(feature))
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "07_partial_dependence_top_descriptors.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def compute_ale_1d(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    feature: str,
    n_bins: int = 10,
) -> pd.DataFrame:
    values = X[feature].to_numpy(dtype=float)
    quantiles = np.unique(np.quantile(values, np.linspace(0, 1, n_bins + 1)))
    if len(quantiles) < 3:
        quantiles = np.linspace(values.min(), values.max(), min(n_bins + 1, len(np.unique(values))))
    rows = []
    effects = []
    centers = []
    for lower, upper in zip(quantiles[:-1], quantiles[1:]):
        if upper <= lower:
            continue
        if upper == quantiles[-1]:
            mask = (values >= lower) & (values <= upper)
        else:
            mask = (values >= lower) & (values < upper)
        if not mask.any():
            continue
        x_lower = X.loc[mask].copy()
        x_upper = X.loc[mask].copy()
        x_lower[feature] = lower
        x_upper[feature] = upper
        local_effect = model.predict(x_upper) - model.predict(x_lower)
        effect = float(np.mean(local_effect))
        center = float((lower + upper) / 2)
        effects.append(effect)
        centers.append(center)
        rows.append(
            {
                "descriptor": feature,
                "bin_lower": lower,
                "bin_upper": upper,
                "bin_center": center,
                "local_effect": effect,
                "n_experiments": int(mask.sum()),
            }
        )
    if not rows:
        return pd.DataFrame()
    ale = np.cumsum(effects)
    ale = ale - np.mean(ale)
    for row, value in zip(rows, ale):
        row["ale_yield_points"] = float(value)
    return pd.DataFrame(rows)


def save_ale_plots(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    shap_df: pd.DataFrame,
    out_dir: Path,
) -> pd.DataFrame:
    top_features = shap_df["descriptor"].head(4).tolist()
    ale_frames = [compute_ale_1d(model, X, feature) for feature in top_features]
    ale_df = pd.concat([frame for frame in ale_frames if not frame.empty], ignore_index=True)
    ale_df.to_csv(out_dir / "ale_top_descriptor_curves.csv", index=False)

    fig, axes = plt.subplots(2, 2, figsize=(9.2, 7.0))
    axes = axes.ravel()
    for ax, feature in zip(axes, top_features):
        feature_df = ale_df[ale_df["descriptor"] == feature]
        ax.plot(
            feature_df["bin_center"],
            feature_df["ale_yield_points"],
            marker="o",
            color="#2F6F73",
            linewidth=2,
        )
        ax.axhline(0, color="#6E6E6E", linewidth=1, linestyle="--")
        ax.set_xlabel(display_label(feature))
        ax.set_ylabel("ALE effect on yield")
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "08_ale_top_descriptors.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    return ale_df


def save_ice_plots(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    shap_df: pd.DataFrame,
    out_dir: Path,
) -> None:
    top_features = shap_df["descriptor"].head(4).tolist()
    fig, axes = plt.subplots(2, 2, figsize=(9.4, 7.1))
    axes = axes.ravel()
    for ax, feature in zip(axes, top_features):
        display = PartialDependenceDisplay.from_estimator(
            model,
            X,
            features=[feature],
            kind="both",
            centered=True,
            subsample=min(63, len(X)),
            random_state=RANDOM_STATE,
            grid_resolution=40,
            ax=ax,
            line_kw={"color": "#9B6A4A", "alpha": 0.16, "linewidth": 0.8},
            pd_line_kw={"color": "#173B57", "linewidth": 2.4},
        )
        for plot_ax in np.ravel(display.axes_):
            if plot_ax is None:
                continue
            for line in plot_ax.get_lines():
                if line.get_label() == "average":
                    line.set_color("#0B2F4A")
                    line.set_alpha(1.0)
                    line.set_linewidth(3.0)
                    line.set_linestyle("-")
                    line.set_zorder(5)
            legend = plot_ax.get_legend()
            if legend is not None:
                for legend_line in legend.get_lines():
                    legend_line.set_color("#0B2F4A")
                    legend_line.set_alpha(1.0)
                    legend_line.set_linewidth(3.0)
                    legend_line.set_linestyle("-")
            plot_ax.set_xlabel(display_label(feature))
            plot_ax.set_ylabel("Centered yield effect")
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "09_ice_top_descriptors.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def _partial_dependence_values(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    features: list[str] | tuple[str, ...],
    grid_resolution: int = 35,
) -> tuple[list[np.ndarray], np.ndarray]:
    feature_indices = [X.columns.get_loc(feature) for feature in features]
    pd_features = [tuple(feature_indices)] if len(feature_indices) == 2 else feature_indices
    result = partial_dependence(
        model,
        X,
        features=pd_features,
        grid_resolution=grid_resolution,
        kind="average",
    )
    grid = result["grid_values"]
    average = np.asarray(result["average"][0], dtype=float)
    return grid, average


def pairwise_interaction_strength(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    feature_a: str,
    feature_b: str,
) -> float:
    (grid_a, grid_b), pdp_ab = _partial_dependence_values(
        model, X, [feature_a, feature_b]
    )
    _, pdp_a = _partial_dependence_values(model, X, [feature_a])
    _, pdp_b = _partial_dependence_values(model, X, [feature_b])
    centered_ab = pdp_ab - np.mean(pdp_ab)
    centered_a = pdp_a.reshape(-1, 1) - np.mean(pdp_a)
    centered_b = pdp_b.reshape(1, -1) - np.mean(pdp_b)
    interaction = centered_ab - centered_a - centered_b
    return float(np.sqrt(np.mean(interaction**2)))


def save_pairwise_interactions(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    shap_df: pd.DataFrame,
    out_dir: Path,
) -> pd.DataFrame:
    top_features = shap_df["descriptor"].head(5).tolist()
    rows = []
    for feature_a, feature_b in itertools.combinations(top_features, 2):
        rows.append(
            {
                "descriptor_a": feature_a,
                "descriptor_b": feature_b,
                "interaction_strength_yield_points": pairwise_interaction_strength(
                    model, X, feature_a, feature_b
                ),
            }
        )
    interaction_df = pd.DataFrame(rows).sort_values(
        "interaction_strength_yield_points", ascending=False
    )
    interaction_df.to_csv(out_dir / "pairwise_interaction_strength.csv", index=False)

    selected_pairs = [tuple(row) for row in interaction_df[["descriptor_a", "descriptor_b"]].head(4).to_numpy()]
    fig, axes = plt.subplots(2, 2, figsize=(9.4, 7.2))
    axes = axes.ravel()
    for ax, (feature_a, feature_b) in zip(axes, selected_pairs):
        (grid_a, grid_b), pdp_ab = _partial_dependence_values(
            model, X, [feature_a, feature_b], grid_resolution=45
        )
        mesh = ax.contourf(grid_a, grid_b, pdp_ab.T, levels=16, cmap="viridis")
        ax.scatter(
            X[feature_a],
            X[feature_b],
            s=12,
            facecolors="#FFFFFF",
            edgecolors="#000000",
            linewidths=0.6,
        )
        ax.set_xlabel(display_label(feature_a))
        ax.set_ylabel(display_label(feature_b))
        sns.despine(ax=ax, left=True, bottom=True)
        colorbar = fig.colorbar(mesh, ax=ax, label="Predicted yield (%)")
        colorbar.outline.set_visible(False)
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "10_pairwise_interaction_surfaces.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    return interaction_df


def save_selected_contour_plots(
    model: RandomForestRegressor,
    X: pd.DataFrame,
    out_dir: Path,
) -> None:
    selected_pairs = [
        ("current", "applied_charge"),
        ("applied_charge", "DMSO_equivalents"),
    ]
    selected_pairs = [
        (feature_a, feature_b)
        for feature_a, feature_b in selected_pairs
        if feature_a in X.columns and feature_b in X.columns
    ]
    fig, axes = plt.subplots(1, len(selected_pairs), figsize=(9.4, 4.2))
    axes = np.atleast_1d(axes)
    for ax, (feature_a, feature_b) in zip(axes, selected_pairs):
        (grid_a, grid_b), pdp_ab = _partial_dependence_values(
            model, X, [feature_a, feature_b], grid_resolution=60
        )
        mesh = ax.contourf(grid_a, grid_b, pdp_ab.T, levels=18, cmap="viridis")
        contours = ax.contour(
            grid_a,
            grid_b,
            pdp_ab.T,
            levels=7,
            colors="white",
            linewidths=0.65,
            alpha=0.75,
        )
        ax.clabel(contours, inline=True, fontsize=6, fmt="%.0f")
        ax.scatter(
            X[feature_a],
            X[feature_b],
            facecolors="#FFFFFF",
            s=14,
            edgecolors="#000000",
            linewidths=0.6,
        )
        ax.set_xlabel(display_label(feature_a))
        ax.set_ylabel(display_label(feature_b))
        sns.despine(ax=ax, left=True, bottom=True)
        colorbar = fig.colorbar(mesh, ax=ax, label="Predicted yield (%)")
        colorbar.outline.set_visible(False)
    clear_plot_titles(fig)
    fig.tight_layout(pad=0.01)
    fig.savefig(out_dir / "14_selected_2d_response_contours.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def save_summary(
    out_dir: Path,
    source: Path,
    n_rows: int,
    columns: list[str],
    perf: ModelPerformance,
    corr_df: pd.DataFrame,
    shap_df: pd.DataFrame,
    perm_df: pd.DataFrame,
    gp_df: pd.DataFrame,
    interaction_df: pd.DataFrame,
) -> None:
    top_shap = shap_df.head(4)
    top_corr = corr_df.head(4)
    top_perm = perm_df.head(4)
    top_gp = gp_df.head(4)
    top_interactions = interaction_df.head(4)
    rank_df = pd.DataFrame({"descriptor": shap_df["descriptor"]})
    rank_df["shap_rank"] = (
        shap_df.set_index("descriptor")["mean_abs_shap_yield_points"]
        .rank(ascending=False, method="average")
        .reindex(rank_df["descriptor"])
        .to_numpy()
    )
    rank_df["permutation_rank"] = (
        perm_df.set_index("descriptor")["mae_increase_mean"]
        .rank(ascending=False, method="average")
        .reindex(rank_df["descriptor"])
        .to_numpy()
    )
    rank_df["gp_rank"] = (
        gp_df.set_index("descriptor")["gp_inverse_lengthscale_importance"]
        .rank(ascending=False, method="average")
        .reindex(rank_df["descriptor"])
        .to_numpy()
    )
    rank_df["mean_rank"] = rank_df[
        ["shap_rank", "permutation_rank", "gp_rank"]
    ].mean(axis=1)
    consensus_top = ", ".join(
        display_label(descriptor)
        for descriptor in rank_df.sort_values("mean_rank")["descriptor"].head(4)
    )
    primary_descriptor_names = ", ".join(display_label(column) for column in columns)

    lines = [
        "# Interpretability analysis",
        "",
        f"Source data: `{source.name}`",
        f"Completed experiments analyzed: {n_rows}",
        f"Primary descriptors: {primary_descriptor_names}",
        "",
        "## Surrogate-model performance",
        "",
        (
            "An ExtraTrees surrogate was evaluated by leave-one-out and shuffled 5-fold "
            "cross-validation to check whether the descriptor space carries predictive "
            "signal before interpreting feature effects."
        ),
        "",
        f"- Leave-one-out R2: {perf.loo_r2:.3f}",
        f"- Leave-one-out MAE: {perf.loo_mae:.2f} yield percentage points",
        f"- 5-fold R2: {perf.kfold_r2:.3f}",
        f"- 5-fold MAE: {perf.kfold_mae:.2f} yield percentage points",
        "",
        "## Main descriptor findings",
        "",
        "Top SHAP descriptors:",
        *[
            f"- {display_label(row.descriptor)}: {row.mean_abs_shap_yield_points:.2f} yield points "
            f"({100 * row.relative_importance:.1f}% relative)"
            for row in top_shap.itertuples()
        ],
        "",
        "Top monotonic associations with yield:",
        *[
            f"- {display_label(row.descriptor)}: Spearman rho = {row.spearman_rho:.2f}, p = {row.p_value:.3g}"
            for row in top_corr.itertuples()
        ],
        "",
        "Top permutation-importance descriptors:",
        *[
            f"- {display_label(row.descriptor)}: MAE increase = {row.mae_increase_mean:.2f} +/- {row.mae_increase_std:.2f}"
            for row in top_perm.itertuples()
        ],
        "",
        "Top GP inverse-lengthscale descriptors:",
        *[
            f"- {display_label(row.descriptor)}: normalized importance = {row.gp_inverse_lengthscale_importance:.2f}"
            for row in top_gp.itertuples()
        ],
        "",
        "Top pairwise interaction surfaces:",
        *[
            f"- {display_label(row.descriptor_a)} x {display_label(row.descriptor_b)}: interaction strength = "
            f"{row.interaction_strength_yield_points:.2f} yield points"
            for row in top_interactions.itertuples()
        ],
        "",
        "## Interpretation",
        "",
        (
            f"The consensus descriptors across SHAP, permutation importance, and the GP "
            f"lengthscale analysis were {consensus_top}. In this electrochemical flow "
            "dataset, these variables define the balance between electron equivalents, "
            "substrate loading, conductivity/supporting electrolyte, co-solvent loading, "
            "and residence-time/current density effects. The high-yield region is therefore "
            "not governed by a single scalar setting, but by a coupled process window in "
            "which adequate charge delivery and mass transport are maintained without "
            "over- or under-diluting the nitro substrate."
        ),
        "",
        (
            "Because the dataset contains 63 experiments, these attributions should be "
            "reported as data-driven hypotheses rather than mechanistic proof. They are "
            "nevertheless useful for explaining which experimentally controlled chemical "
            "and physical descriptors most strongly shaped the generated optimization space."
        ),
        "",
        "## Generated files",
        "",
        "- `01_yield_progress.png`: yield and best-yield trajectory",
        "- `02_descriptor_correlation_heatmap.png`: monotonic descriptor relationships",
        "- `03_shap_global_importance.png`: global SHAP importance",
        "- `04_shap_directional_summary.png`: directional SHAP summary",
        "- `05_permutation_importance.png`: model-agnostic importance",
        "- `06_observed_descriptor_trends.png`: observed trends for leading descriptors",
        "- `07_partial_dependence_top_descriptors.png`: surrogate response curves",
        "- `08_ale_top_descriptors.png`: accumulated local effects",
        "- `09_ice_top_descriptors.png`: individual conditional expectation curves",
        "- `10_pairwise_interaction_surfaces.png`: top two-descriptor response surfaces",
        "- `11_pca_descriptor_score_yield.png`: PCA descriptor score plot colored by yield",
        "- `12_pls_descriptor_score_yield.png`: PLS regression score plot colored by yield",
        "- `13_shap_dependence_top_descriptors.png`: SHAP dependence plots for leading descriptors",
        "- `14_selected_2d_response_contours.png`: selected current/charge/DMSO response contours",
        "- `*_descriptor_importance.csv`: numeric tables for supplement or response letter",
        "- `ale_top_descriptor_curves.csv`: numeric ALE curves",
        "- `pairwise_interaction_strength.csv`: ranked two-descriptor interaction strengths",
    ]
    (out_dir / "interpretability_summary.md").write_text(
        "\n".join(lines) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate descriptor importance and SHAP interpretability analysis."
    )
    parser.add_argument("--experiments", default="Experiments_bo.xlsx", type=Path)
    parser.add_argument("--parameters", default="parameter.json", type=Path)
    parser.add_argument(
        "--out-dir", default=Path("analysis/interpretability"), type=Path
    )
    args = parser.parse_args()

    sns.set_theme(style="whitegrid", context="paper")
    torch.set_default_dtype(torch.float64)
    np.random.seed(RANDOM_STATE)
    torch.manual_seed(RANDOM_STATE)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    raw_df = pd.read_excel(args.experiments, engine="openpyxl")
    df = raw_df.dropna(subset=[TARGET]).copy().reset_index(drop=True)
    active_columns, parameter_config = read_parameter_columns(args.parameters, df)

    X_primary = df[active_columns].astype(float)
    y = df[TARGET].astype(float)
    X_interpretable = add_derived_descriptors(X_primary)

    performance = cross_validated_performance(X_primary, y)
    model = fit_tree_model(X_primary, y)

    save_observed_progress(df, args.out_dir)
    corr_df = save_correlation_heatmap(X_interpretable, y, args.out_dir)
    shap_df = save_shap_outputs(model, X_primary, args.out_dir)
    save_dimension_reduction_plots(X_primary, y, args.out_dir)
    save_shap_dependence_plots(model, X_primary, shap_df, args.out_dir)
    perm_df = save_permutation_importance(model, X_primary, y, args.out_dir)
    gp_df = gp_lengthscale_importance(X_primary, y, parameter_config)
    gp_df.to_csv(args.out_dir / "gp_lengthscale_descriptor_importance.csv", index=False)
    save_response_plots(model, X_primary, y, shap_df, args.out_dir)
    save_ale_plots(model, X_primary, shap_df, args.out_dir)
    save_ice_plots(model, X_primary, shap_df, args.out_dir)
    interaction_df = save_pairwise_interactions(model, X_primary, shap_df, args.out_dir)
    save_selected_contour_plots(model, X_primary, args.out_dir)
    save_summary(
        args.out_dir,
        args.experiments,
        len(df),
        active_columns,
        performance,
        corr_df,
        shap_df,
        perm_df,
        gp_df,
        interaction_df,
    )
    print(f"Wrote interpretability analysis to {args.out_dir}")
    print(f"Leave-one-out R2={performance.loo_r2:.3f}, MAE={performance.loo_mae:.2f}")


if __name__ == "__main__":
    main()
