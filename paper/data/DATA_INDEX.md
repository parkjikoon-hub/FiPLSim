# 논문용 Uni/Bi 시뮬레이션 데이터 인덱스
> **v1.0** | 최종 업데이트: **2026-03-31 10:00** | 작성자: Claude Code

## 변경 이력
| 버전 | 일시 | 주요 변경 |
|------|------|---------|
| v1.0 | 2026-03-31 10:00 | 초기 작성 — 6변형 + 보강 시뮬 통합 |

---

## 1. 개요

이 폴더는 **단방향(uni) vs 양방향(bi) 가지배관 시뮬레이션** 논문 작성에 필요한
모든 데이터를 하나로 모은 것입니다.

- **단방향 모델**: 이경엘보(K=0.53) 기반 — T분기 모델 데이터는 제외
- **양방향 모델**: T분기(K3=1.0, K_TEE=1.06) 기반
- **물성치**: NFPA 13 통합 (epsilon=0.046mm, rho=1000, nu=1.002e-6)

---

## 2. 폴더 구조

```
paper_uni_bi_data/
│
├── 01_8H_2.1m/          ← 기본 (8헤드, 간격 2.1m)
│   ├── data/            ← CSV 8개 (A1~E1)
│   ├── figures/         ← PNG 10개 (그래프)
│   ├── run_manifest.json
│   └── smoke_test_log.txt
│
├── 02_7H_2.1m/          ← 변형1 (7헤드, 간격 2.1m)
│   └── (동일 구조)
│
├── 03_8H_2.3m/          ← 변형2 (8헤드, 간격 2.3m)
│   └── (동일 구조)
│
├── 04_7H_2.3m/          ← 변형3 (7헤드, 간격 2.3m)
│   └── (동일 구조)
│
├── 05_8H_2.6m/          ← 변형4 (8헤드, 간격 2.6m)
│   └── (동일 구조)
│
├── 06_7H_2.6m/          ← 변형5 (7헤드, 간격 2.6m)
│   └── (동일 구조)
│
├── sensitivity_analysis/ ← 보강 시뮬레이션 (리뷰어 방어용)
│   ├── k_sensitivity/    ← Phase 1: K값 민감도 (28건)
│   ├── placement_patterns/ ← Phase 2: 결함 배치 패턴 (8건)
│   ├── bi_imbalance/     ← Phase 3: 양방향 불균형 (8건)
│   ├── overall_summary.csv  ← 전체 종합 (26행)
│   ├── analysis_note.md     ← 분석 보고서
│   ├── run_manifest.json
│   ├── run_supplementary_sims.py
│   └── run_supplementary_sims.zip
│
├── scripts/             ← 시뮬레이션 스크립트
│   ├── run_uni_bi_final.py        ← 6변형 메인 스크립트
│   ├── run_supplementary_sims.py  ← 보강 시뮬 스크립트
│   └── plot_uni_bi_results.py     ← 그래프 생성 스크립트
│
└── DATA_INDEX.md        ← 이 문서
```

---

## 3. 6변형 시뮬레이션 결과 요약

### 시나리오별 P_REF (단방향 기준 임계압력)

| 폴더 | 헤드 수 | 간격 | P_REF (MPa) |
|------|--------|------|-------------|
| 01_8H_2.1m | 8 | 2.1m | 0.529356 |
| 02_7H_2.1m | 7 | 2.1m | 0.453674 |
| 03_8H_2.3m | 8 | 2.3m | 0.537488 |
| 04_7H_2.3m | 7 | 2.3m | 0.460871 |
| 05_8H_2.6m | 8 | 2.6m | 0.549687 |
| 06_7H_2.6m | 7 | 2.6m | 0.471668 |

### 캠페인 설명 (각 변형에 공통)

| 캠페인 | CSV 파일명 | 내용 |
|--------|-----------|------|
| A1 | A1_design_flow_deterministic.csv | 설계유량 결정론 — 비드/결함수별 말단압력 |
| A2 | A2_flow_sweep_deterministic.csv | 유량 스윕 결정론 |
| B1 | B1_design_flow_critical_pressure.csv | 설계유량 임계압력 (이분법) |
| B2 | B2_flow_bead_critical_map.csv | 유량-비드 임계압력 지도 |
| C1 | C1_reliability_pressure_transition.csv | MC 신뢰성 — 압력 전이 (S-curve) |
| C2 | C2_baseline_pressure_transition.csv | 기준선 압력 전이 |
| D1 | D1_reliability_flow_transition.csv | MC 신뢰성 — 유량 전이 |
| E1 | E1_position_sensitivity.csv | 결함 위치 민감도 |

### 그래프 설명 (각 변형에 공통)

| 그래프 | PNG 파일명 | 내용 |
|--------|-----------|------|
| A1-1 | A1_terminal_vs_bead_by_defect.png | 비드높이 vs 말단압력 (결함수별) |
| A1-2 | A1_loss_breakdown.png | 손실 분해 (배관/이음쇠/레듀서) |
| A2 | A2_terminal_vs_flow.png | 유량 vs 말단압력 |
| B1 | B1_critical_pressure.png | 임계압력 비교 (uni vs bi) |
| B2 | B2_critical_pressure_map.png | 유량-비드 임계압력 지도 |
| C1-1 | C1_scurve_pressure_std050.png | S-curve (std=0.5) |
| C1-2 | C1_scurve_std_sensitivity.png | S-curve 표준편차 민감도 |
| C2 | C2_baseline.png | 기준선 S-curve |
| D1 | D1_scurve_flow_std050.png | 유량 S-curve |
| E1 | E1_position_sensitivity.png | 결함 위치별 영향도 |

---

## 4. 보강 시뮬레이션 (sensitivity_analysis/) 요약

### Phase 1: K값 민감도 — 28건 전부 OK
- K_ELBOW 4수준 × K3_bi 3수준 × K_TEE 3수준 + Corner 4건
- 최악 케이스(K_ELBOW=0.75, K_TEE=0.90)에서도 uni가 1.1 kPa 우수

### Phase 2: 결함 배치 패턴 — 8건 전부 OK
- worst / uniform / downstream / upstream 4패턴 × 2시나리오
- 모든 패턴에서 bi fail_rate ≥ uni fail_rate

### Phase 3: 양방향 불균형 — 8건 전부 OK
- 대칭(BI-0) / +1헤드(BI-1) / +2헤드(BI-2) / 편측결함(BI-3) × 2시나리오
- 비대칭 시 bi 불리함 2.7~4.4배 확대

**종합: 44건 전부 PASS — 결론 강건**

---

## 5. 핵심 결론

> 6변형(7/8헤드 × 2.1/2.3/2.6m) 전부에서 **양방향이 단방향보다 불리**.
> K값 민감도·결함 배치 패턴·양방향 비대칭 검증 결과, 결론은 **강건**함이 입증됨.

---

## 6. 사용 방법

### 그래프 재생성
```bash
PYTHONIOENCODING=utf-8 python3 scripts/plot_uni_bi_results.py --datadir 01_8H_2.1m
```

### 시뮬레이션 재실행 (예: 8헤드/2.1m)
```bash
PYTHONIOENCODING=utf-8 python3 scripts/run_uni_bi_final.py --heads 8 --spacing 2.1 --outdir 01_8H_2.1m --case ALL
```

### 보강 시뮬레이션 재실행
```bash
PYTHONIOENCODING=utf-8 python3 scripts/run_supplementary_sims.py --phase 1 2 3
```
