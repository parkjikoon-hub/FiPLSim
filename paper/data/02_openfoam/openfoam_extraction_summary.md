# OpenFOAM 검증 데이터 추출 요약

작성일: 2026-03-31

## 데이터 원천

- 재추출 CSV: [openfoam_patch_metrics_reextracted.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/openfoam_patch_metrics_reextracted.csv)
- 재추출 JSON: [openfoam_patch_metrics_reextracted.json](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/openfoam_patch_metrics_reextracted.json)
- FiPLSim 비교 원시 CSV: [fiplsim_openfoam_comparison_raw.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/fiplsim_openfoam_comparison_raw.csv)
- 원본 케이스 경로: `C:\Users\INTEL\Documents\Playground\_analysis_sim\openfoam_validation`

## 재추출 실행

아래 스크립트를 2026-03-31에 WSL에서 다시 실행하였다.

- `C:\Users\INTEL\Documents\Playground\_analysis_sim\openfoam_validation\extract_openfoam_metrics.py`

[출처: `extract_openfoam_metrics.py` WSL 재실행 로그, 2026-03-31]

## 핵심 추출 결과

- baseline `dp_inlet_to_outlet2`: `9.718348 Pa`
- `1.5 mm bead` `dp_inlet_to_outlet2`: `10.032807 Pa`
- `3.0 mm bead` `dp_inlet_to_outlet2`: `12.049442 Pa`

- baseline 대비 비율
  - `1.5 mm`: `1.032357`
  - `3.0 mm`: `1.239865`

- total outflow
  - baseline: `0.003114245 m3/s`
  - `1.5 mm`: `0.003098159 m3/s`
  - `3.0 mm`: `0.002992385 m3/s`

[출처: [openfoam_patch_metrics_reextracted.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/openfoam_patch_metrics_reextracted.csv), 2026-03-31 재추출]

## FiPLSim 대비 요약

- moderate bead 평균 `dp-ratio difference`: `43.792%`
- severe bead 평균 `dp-ratio difference`: `87.988%`
- moderate bead 평균 `K-ratio difference`: `42.236%`
- severe bead 평균 `K-ratio difference`: `73.586%`

[출처: [fiplsim_openfoam_comparison_raw.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/fiplsim_openfoam_comparison_raw.csv), 2026-03-31 재집계]

## 논문 반영용 문장

`OpenFOAM 기반 T-junction 해석에서 baseline, 1.5 mm bead, 3.0 mm bead 조건의 선택 압력강하 지표(dp_inlet_to_outlet2)는 각각 9.718 Pa, 10.033 Pa, 12.049 Pa로 증가하였다.`

`FiPLSim과 OpenFOAM의 baseline-relative 압력강하 비 비교에서 moderate bead의 평균 차이는 43.792%, severe bead의 평균 차이는 87.988%로 나타났다.`

## 해석상 주의점

- OpenFOAM 결과는 `전체 네트워크 validation`이 아니라 `국부손실 메커니즘 보조 검증`으로 해석해야 한다.
- absolute value 일치보다는 `bead 증가에 따라 압력강하가 증가하는 방향성`과 `국부손실 증가 메커니즘`을 확인하는 용도로 쓰는 것이 적절하다.

[산출근거: OpenFOAM 대비 FiPLSim의 절대값 차이가 moderate/severe 조건에서 크게 남아 있으므로, 정량 validation보다 mechanism-level support로 위치시키는 것이 타당함]

