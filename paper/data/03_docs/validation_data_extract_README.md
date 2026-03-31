# EPANET / OpenFOAM 검증 데이터 추출 폴더

작성일: 2026-03-31

## 폴더 목적

이 폴더는 논문 검증 파트에 사용할 `EPANET` 및 `OpenFOAM` 관련 원시 비교 데이터와 재추출 요약을 모아둔 경로이다.

## 파일 구성

- `epanet_comparison_summary_raw.csv`
  - EPANET 검증 요약 원시 CSV
- `epanet_comparison_branch_terminals_raw.csv`
  - EPANET 분기별 말단압 비교 원시 CSV
- `openfoam_patch_metrics_reextracted.csv`
  - OpenFOAM 원본 케이스에서 2026-03-31에 다시 추출한 patch metric CSV
- `openfoam_patch_metrics_reextracted.json`
  - OpenFOAM 원본 케이스에서 2026-03-31에 다시 추출한 JSON
- `fiplsim_openfoam_comparison_raw.csv`
  - FiPLSim-OpenFOAM 비교 원시 CSV
- `epanet_extraction_summary.md`
  - EPANET 비교 결과 재정리 문서
- `openfoam_extraction_summary.md`
  - OpenFOAM 비교 결과 재정리 문서
- `validation_extraction_overview.md`
  - 논문 반영 관점의 통합 정리 문서

## 추출 방식

- `EPANET`
  - 현재 실행 환경에는 `EPyT` 재실행 패키지가 없어, 기존 EPANET 검증 실행 결과의 원시 CSV에서 핵심 수치를 다시 추출하였다.
- `OpenFOAM`
  - `C:\Users\INTEL\Documents\Playground\_analysis_sim\openfoam_validation\extract_openfoam_metrics.py`를 WSL에서 직접 재실행하여 patch metric을 다시 추출하였다.

