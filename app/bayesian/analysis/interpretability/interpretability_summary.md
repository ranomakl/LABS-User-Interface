# Interpretability analysis

Source data: `Experiments_bo.xlsx`
Completed experiments analyzed: 63
Primary descriptors: Current (A), Applied charge (F), Flow rate (mL min$^{-1}$), DMSO equivalents (equiv), Supporting electrolyte concentration (mol L$^{-1}$), Nitroarene concentration (mol L$^{-1}$), Reaction volume (mL)

## Surrogate-model performance

An ExtraTrees surrogate was evaluated by leave-one-out and shuffled 5-fold cross-validation to check whether the descriptor space carries predictive signal before interpreting feature effects.

- Leave-one-out R2: 0.968
- Leave-one-out MAE: 3.00 yield percentage points
- 5-fold R2: 0.919
- 5-fold MAE: 4.12 yield percentage points

## Main descriptor findings

Top SHAP descriptors:
- Current (A): 6.16 yield points (33.0% relative)
- Applied charge (F): 5.47 yield points (29.3% relative)
- DMSO equivalents (equiv): 3.85 yield points (20.6% relative)
- Nitroarene concentration (mol L$^{-1}$): 1.87 yield points (10.0% relative)

Top monotonic associations with yield:
- DMSO equivalents (equiv): Spearman rho = 0.77, p = 1.6e-13
- Charge-concentration product (F mol L$^{-1}$): Spearman rho = 0.65, p = 7.59e-09
- Nitroarene concentration (mol L$^{-1}$): Spearman rho = 0.58, p = 6.8e-07
- Applied charge (F): Spearman rho = 0.48, p = 6.55e-05

Top permutation-importance descriptors:
- Current (A): MAE increase = 5.08 +/- 0.68
- Applied charge (F): MAE increase = 4.06 +/- 0.61
- DMSO equivalents (equiv): MAE increase = 2.25 +/- 0.41
- Nitroarene concentration (mol L$^{-1}$): MAE increase = 0.93 +/- 0.24

Top GP inverse-lengthscale descriptors:
- Current (A): normalized importance = 0.38
- Applied charge (F): normalized importance = 0.18
- Nitroarene concentration (mol L$^{-1}$): normalized importance = 0.15
- DMSO equivalents (equiv): normalized importance = 0.10

Top pairwise interaction surfaces:
- Current (A) x Applied charge (F): interaction strength = 0.98 yield points
- Current (A) x DMSO equivalents (equiv): interaction strength = 0.54 yield points
- Applied charge (F) x DMSO equivalents (equiv): interaction strength = 0.48 yield points
- Applied charge (F) x Nitroarene concentration (mol L$^{-1}$): interaction strength = 0.38 yield points

## Interpretation

The consensus descriptors across SHAP, permutation importance, and the GP lengthscale analysis were Current (A), Applied charge (F), DMSO equivalents (equiv), Nitroarene concentration (mol L$^{-1}$). In this electrochemical flow dataset, these variables define the balance between electron equivalents, substrate loading, conductivity/supporting electrolyte, co-solvent loading, and residence-time/current density effects. The high-yield region is therefore not governed by a single scalar setting, but by a coupled process window in which adequate charge delivery and mass transport are maintained without over- or under-diluting the nitro substrate.

Because the dataset contains 63 experiments, these attributions should be reported as data-driven hypotheses rather than mechanistic proof. They are nevertheless useful for explaining which experimentally controlled chemical and physical descriptors most strongly shaped the generated optimization space.

## Generated files

- `01_yield_progress.png`: yield and best-yield trajectory
- `02_descriptor_correlation_heatmap.png`: monotonic descriptor relationships
- `03_shap_global_importance.png`: global SHAP importance
- `04_shap_directional_summary.png`: directional SHAP summary
- `05_permutation_importance.png`: model-agnostic importance
- `06_observed_descriptor_trends.png`: observed trends for leading descriptors
- `07_partial_dependence_top_descriptors.png`: surrogate response curves
- `08_ale_top_descriptors.png`: accumulated local effects
- `09_ice_top_descriptors.png`: individual conditional expectation curves
- `10_pairwise_interaction_surfaces.png`: top two-descriptor response surfaces
- `11_pca_descriptor_score_yield.png`: PCA descriptor score plot colored by yield
- `12_pls_descriptor_score_yield.png`: PLS regression score plot colored by yield
- `13_shap_dependence_top_descriptors.png`: SHAP dependence plots for leading descriptors
- `14_selected_2d_response_contours.png`: selected current/charge/DMSO response contours
- `*_descriptor_importance.csv`: numeric tables for supplement or response letter
- `ale_top_descriptor_curves.csv`: numeric ALE curves
- `pairwise_interaction_strength.csv`: ranked two-descriptor interaction strengths
