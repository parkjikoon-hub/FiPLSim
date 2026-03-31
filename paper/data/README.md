# paper_validation_dataset_epanet_openfoam_2026-03-31

작성일: 2026-03-31

## 폴더 목적

이 폴더는 이번 논문 작성에 사용한 `EPANET` 및 `OpenFOAM` 추출 데이터를 한곳에 모아 관리하기 위한 검증 데이터 패키지이다.

논문 작성과 심사 대응에서 바로 사용할 수 있도록 다음 원칙으로 정리하였다.

- `01_epanet`
  - EPANET 추출 원자료와 요약 문서만 별도 보관
- `02_openfoam`
  - OpenFOAM 재추출 자료와 FiPLSim-OpenFOAM 비교 자료를 함께 보관
- `03_docs`
  - 전체 검증 자료를 설명하는 상위 문서 보관

## 폴더 구조

### 01_epanet

- `epanet_comparison_branch_terminals_raw.csv`
- `epanet_comparison_summary_raw.csv`
- `epanet_extraction_summary.md`

역할:

- FiPLSim의 `network-level cross-check`
- 논문에서 `EPANET은 solver 기본 검증`이라는 역할로 사용할 때 참조

### 02_openfoam

- `openfoam_patch_metrics_reextracted.csv`
- `openfoam_patch_metrics_reextracted.json`
- `openfoam_extraction_summary.md`
- `fiplsim_openfoam_comparison_raw.csv`
- `fiplsim_openfoam_comparison_table.csv`
- `fiplsim_openfoam_comparison_summary.md`
- `openfoam_patch_metrics.csv`
- `openfoam_patch_metrics_extended.csv`

역할:

- OpenFOAM 재추출 결과
- FiPLSim과 OpenFOAM의 비교용 참고 자료
- 논문에서 `mechanism-level supplementary verification`로 사용할 때 참조

주의:

- `reextracted`가 붙은 파일은 2026-03-31 기준 재추출본이다.
- `data_figure/openfoam_reference`에서 가져온 파일은 비교 설명을 돕는 참고용 복사본이다.

### 03_docs

- `validation_extraction_overview.md`
- `validation_data_extract_README.md`

역할:

- 이번 검증 데이터 패키지 전체 설명
- EPANET과 OpenFOAM의 사용 범위를 정리할 때 참조

## 가장 중요한 해석 원칙

1. `EPANET`
   - revised uni elbow 최종 모델의 직접 재검증 자료가 아님
   - 기존 raw validation 결과를 재집계한 `solver-level cross-check`
2. `OpenFOAM`
   - 전체 네트워크 validation 자료가 아님
   - T-junction 및 bead에 따른 `local loss trend` 확인용

## 권장 사용 순서

1. 전체 취지를 먼저 `03_docs/validation_extraction_overview.md`에서 확인
2. EPANET은 `01_epanet/epanet_extraction_summary.md`부터 확인
3. OpenFOAM은 `02_openfoam/openfoam_extraction_summary.md`와 `02_openfoam/fiplsim_openfoam_comparison_summary.md`를 먼저 확인

## 관리 메모

- 원본 폴더는 그대로 유지하고, 이 폴더는 논문 작업용 복사본으로 관리한다.
- 추후 revised geometry 기준 신규 EPANET 재검증을 수행하면, 별도 날짜 폴더를 새로 만드는 것을 권장한다.
