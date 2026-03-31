# EPANET 검증 데이터 추출 요약

작성일: 2026-03-31

## 데이터 원천

- 원시 요약 CSV: [epanet_comparison_summary_raw.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/epanet_comparison_summary_raw.csv)
- 원시 분기 말단 CSV: [epanet_comparison_branch_terminals_raw.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/epanet_comparison_branch_terminals_raw.csv)
- 원본 생성 경로: `C:\Users\INTEL\Documents\Playground\_analysis_sim\epanet_validation\data`

## 핵심 추출 결과

- 비교 케이스 수: `9`
- worst branch 일치 여부: `9/9 케이스 일치`
- `max node error` 범위: `0.2548% ~ 0.9360%`
- `mean node error` 범위: `0.1176% ~ 0.3126%`
- `mean(mean node error)`: `0.2389%`

[출처: [epanet_comparison_summary_raw.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/epanet_comparison_summary_raw.csv), 2026-03-31 재집계]

## 말단압 분기별 확인

규정 경계 케이스 `E-B0-1`에서 branch 0~3의 FiPLSim 말단압은 각각
`0.139383`, `0.114253`, `0.102919`, `0.099977 MPa`였고,
EPANET은 각각
`0.138647`, `0.113395`, `0.102000`, `0.099041 MPa`였다.

worst branch는 양 해석기 모두 `branch 3`으로 일치하였다.

[출처: [epanet_comparison_branch_terminals_raw.csv](C:/Users/INTEL/Documents/Playground/uni_bi_simulation_paperwork/validation_data_extract_2026-03-31/epanet_comparison_branch_terminals_raw.csv), 2026-03-31 재확인]

## 논문 반영용 문장

`FiPLSim과 EPANET의 네트워크 수준 비교 결과, 총 9개 검증 케이스에서 worst branch가 모두 일치하였고, max node error는 0.255%~0.936%, mean node error는 0.118%~0.313% 범위로 나타났다.`

## 해석상 주의점

- 현재 재정리한 EPANET 수치는 `기존 EPANET 검증 실행 결과`를 다시 읽어 정리한 것이다.
- 즉, `수정된 uni elbow 모델` 기준의 신규 EPANET 재실행 결과는 아니다.
- 따라서 본 수치는 `FiPLSim 수리해석 엔진의 기본 네트워크 재현성`을 지지하는 자료로는 유효하지만,
  이번 `revised uni vs bi` 결론 자체를 직접 검증한 최신 EPANET 결과라고 쓰면 안 된다.

[산출근거: 현재 실행 환경에서 EPANET용 `EPyT` 패키지가 부재하여 기존 raw CSV를 기준으로 재집계함]

