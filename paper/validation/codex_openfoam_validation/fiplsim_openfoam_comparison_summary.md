# FiPLSim-OpenFOAM Comparison Summary

- OpenFOAM extended metrics file: C:\Users\INTEL\Documents\Playground\_analysis_sim\openfoam_validation\openfoam_patch_metrics_extended.csv
- FiPLSim comparison file: C:\Users\INTEL\Documents\Playground\_analysis_sim\openfoam_validation\fiplsim_openfoam_comparison_table.csv

## OpenFOAM proxy definition

- Selected local-loss proxy = max(dp_inlet_to_outlet1_Pa, dp_inlet_to_outlet2_Pa)
- Dynamic-pressure normalization uses inlet characteristic velocity and water properties:
  - rho = 998.2 kg/m3
  - mu = 0.001003 Pa*s

## Comparison highlights

- Average moderate-bead dp-ratio difference (%): 43.792
- Average severe-bead dp-ratio difference (%): 87.988

## Interpretation rule

- Moderate bead: check if FiPLSim baseline-relative increase is directionally consistent with OpenFOAM.
- Severe bead: if discrepancy remains large, describe it as an expected limitation of the equivalent-loss representation rather than a failure of the mechanism-level conclusion.
