# EPANET / OpenFOAM 검증 데이터 통합 정리

작성일: 2026-03-31

## 1. 이번 추출 작업의 의미

이번 작업은 논문 검증 파트에 사용할 `EPANET`과 `OpenFOAM` 데이터를 제가 직접 다시 정리한 것이다.

- `EPANET`
  - 기존 검증 실행 결과의 raw CSV에서 핵심 수치를 다시 추출
- `OpenFOAM`
  - 원본 케이스에서 patch metric을 직접 재추출

## 2. 현재 논문에서의 역할 구분

### 2.1 EPANET

`EPANET`은 `FiPLSim 수리해석 엔진의 네트워크 수준 기본 재현성`을 보여주는 자료로 사용 가능하다.

- 9개 케이스에서 worst branch 일치
- max node error `0.255%~0.936%`
- mean node error `0.118%~0.313%`

[출처: [epanet_extraction_summary.md](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/epanet_extraction_summary.md), 2026-03-31]

### 2.2 OpenFOAM

`OpenFOAM`은 `국부손실 증가 메커니즘의 방향성`을 지지하는 자료로 사용 가능하다.

- bead 높이 증가에 따라 압력강하 증가
- absolute ratio 차이는 커서 전면적 validation 자료로 쓰기는 어려움

[출처: [openfoam_extraction_summary.md](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/openfoam_extraction_summary.md), 2026-03-31]

## 3. 논문 반영 권고

### 3.1 바로 사용할 수 있는 부분

- `EPANET`:
  - `FiPLSim solver의 기본 네트워크 정확도`
- `OpenFOAM`:
  - `T-junction 및 bead에 따른 local loss trend`

### 3.2 그대로 쓰면 위험한 부분

- `EPANET`:
  - 현재 수치를 `revised uni elbow model`에 대한 최신 직접 검증이라고 쓰면 안 됨
- `OpenFOAM`:
  - 전체 네트워크 validation처럼 쓰면 안 됨

## 4. 최종 판단

현재 기준으로는 아래처럼 정리하는 것이 가장 안전하다.

`EPANET 비교는 FiPLSim의 네트워크 수준 기본 해석 신뢰성을 보여주는 독립 교차검증으로 활용하고, OpenFOAM 비교는 T-junction 및 bead 결함에 따른 국부손실 메커니즘을 보조적으로 설명하는 자료로 활용한다.`

`다만 revised uni elbow vs bi tee 결론 자체를 한 단계 더 강하게 방어하려면, 추후 revised geometry 기준 EPANET 재실행 검증을 추가하는 것이 가장 효과적이다.`

[산출근거: 현재 EPANET 데이터는 기존 raw validation 결과 기반이며, OpenFOAM은 국부 메커니즘 수준 비교로 해석하는 것이 결과 범위와 가장 잘 부합함]

