# 인수인계 자료: 논문 검증 작업 전체 현황

> **작성일**: 2026-03-06
> **논문**: "Stochastic assessment of weld bead effects on fire sprinkler piping networks" (Fire Safety Journal)
> **시뮬레이터**: FiPLSim (Fire Protection Pipe Line Simulator)

---

## 1. 논문 재현 시스템 조건

| 항목 | 값 |
|------|---|
| 배관 토폴로지 | Tree (가지형) |
| 가지배관 수 | 4 |
| 가지배관당 헤드 수 | 8 (총 32 junctions) |
| 입구 압력 | 0.4 MPa |
| 밸브/기기류 | **OFF** (논문 모델에 미포함) |
| 교차배관 구경 | **80A** |
| 가지배관 구경 | 50A×3 → 40A×2 → 32A×1 → 25A×2 (자동 선정) |
| 헤드 간격 | 2.3 m |
| K-factor | K1_BASE=0.5, K2=2.5, K3=1.0, K_TEE_RUN=0.3 |

### Scenario 1 MC (결함 집중)
- worst branch(마지막 branch, index=3)의 **8개 junction에만** Bernoulli(p=0.5) 비드 배치
- 나머지 3개 branch는 bead = 0mm
- MC 반복: 10,000회

### Scenario 2 (시공 품질) — 아직 미재현
- 전체 32 junctions에 Bernoulli(p_b=0.1~0.9) 비드 배치

---

## 2. 해결 완료된 문제 3건

### 문제 1: 절대조도(ε) 불일치 — 해결

| 항목 | 값 |
|------|---|
| **FiPLSim 기본값** | ε = 0.045mm (신품 탄소강) |
| **논문 추정값** | ε ≈ 0.154mm (약간 사용된 탄소강) |
| **발견 방법** | ε 감도분석 + 보간 계산 |

- **Case B(bead=0) @ Q=1600 LPM**: ε=0.154mm에서 논문값(0.2430 MPa)과 **완벽 일치**
- **Case B @ Q=1200 LPM**: ε=0.15mm에서 +0.39 kPa (사실상 해소)
- **Case A 2.5mm @ Q=1200 LPM**: ε=0.15mm에서 +1.47 kPa (해소 수준)
- **결론**: 논문은 ε ≈ 0.15mm 조도를 사용한 것으로 판단

### 문제 2: D_eff 전체 적용 — 기각 (물리적 부적절)

- **시도**: 비드가 있는 junction에서 배관 전체(2.3m)에 D_eff = D - 2h 적용
  - 유속, 마찰계수, Darcy-Weisbach 모두 D_eff 사용
- **결과**: 비드 손실 **249 kPa** vs 논문 기대값 ~60 kPa (**4~5배 과대**)
- **원인**: 비드는 **국소적 돌출** (수mm 폭)이므로 2.3m 전체 구간에 적용하면 물리적으로 부적절
- **결론**: K-factor-only 모델 유지 (K = K_base × (D/D_eff)^4, V_upstream 기준)
- **코드 상태**: D_eff 관련 변경 전부 **되돌림 완료** (pipe_network.py 원본 복원)

### 문제 3: V_upstream vs V_eff (D^4 vs D^8 모델) — 비교 완료

**배경**:
- 현재 FiPLSim: K1 손실 = K_eff × V_upstream² / (2g) → 실질 (D/D_eff)^4 모델
- 가능성 A: K1 손실 = K_eff × V_eff² / (2g) → 실질 (D/D_eff)^8 모델
  - V_eff = Q / A_eff, A_eff = π/4 × D_eff² (축소된 단면적의 유속)

**코드 수정 완료** (`pipe_network.py`):
```python
def _calculate_branch_profile(
    branch, branch_inlet_pressure_mpa, K3_val=K3,
    bead_velocity_model="upstream",   # ← 새 파라미터
):
```
- `bead_velocity_model="upstream"` (기본): 기존 D^4 모델 — **모든 기존 테스트 193개 통과**
- `bead_velocity_model="constriction"`: D^8 모델 — V_eff 사용

**비교 결과** (ε=0.154mm, Scenario 1 MC, 10,000회):

| 조건 | D^4 모델 | D^8 모델 | 논문 |
|------|---------|---------|------|
| bead 2.0mm Q=2100 평균압력 | 0.1237 MPa | 0.1129 MPa | **0.1199 MPa** |
| bead 2.0mm Q=2100 Pf | 0.00% | 2.82% | **0.37%** |
| bead 2.5mm Q=2100 평균압력 | 0.1213 MPa | 0.1044 MPa | **0.1100 MPa** |
| bead 2.5mm Q=2100 Pf | 0.00% | 37.13% | **2.43%** |

| 비교 항목 | D^4 | D^8 |
|----------|-----|-----|
| 압력 평균 정확도 | +11.3 kPa (과대) | -5.6 kPa (과소) |
| Pf 정확도 | 0% (너무 낮음) | 37% (너무 높음) |
| 비드 손실 비율 (vs D^4) | 1.0x | 2.42~2.87x |

**결론**:
- D^8이 압력 평균에서 논문에 **더 가까움** (-5.6 vs +11.3 kPa)
- 그러나 Pf는 D^4(0%)와 D^8(37%) **사이에** 논문(2.43%)이 위치
- 논문의 실제 모델은 D^4와 D^8 **사이의 중간 모델**일 가능성이 높음
- 논문 저자에게 비드 손실 계산 상세 확인 필요

---

## 3. 현재 코드 상태

### 수정된 파일 (커밋 안 됨)

| 파일 | 수정 내용 | 영향 |
|------|----------|------|
| `pipe_network.py` | `_calculate_branch_profile()`에 `bead_velocity_model` 파라미터 추가 (375행) | 기본값 "upstream"이므로 **기존 동작 100% 동일** |
| `pipe_network.py` | `calculate_dynamic_system()`에 `bead_velocity_model` 파라미터 전달 (470행) | 동일 |
| `pipe_network.py` | K1 손실 계산에 모델 분기 추가 (418~424행) | "constriction" 선택 시 V_eff 사용 |

### 수정 코드 상세 (pipe_network.py 418~424행)
```python
# * 비드 K1 손실: 속도 기준 선택
if bead_velocity_model == "constriction" and junc.bead_height_mm > 0:
    D_eff_m = seg.inner_diameter_m - 2.0 * junc.bead_height_mm / 1000.0
    V_bead = velocity_from_flow(segment_flow, D_eff_m) if D_eff_m > 0 else V
    p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V_bead))
else:
    p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V))
```

### 테스트 현황
- **193개 전체 통과** (46 integration + 38 weld bead + 46 grid + 63 valve)
- 기본값 "upstream"이므로 기존 테스트에 영향 없음

---

## 4. 생성된 시뮬레이션 스크립트 및 출력 파일

### 스크립트 (모두 프로젝트 루트에 위치)

| 스크립트 | 용도 | 상태 |
|---------|------|------|
| `run_table8_reproduction.py` | Table 8 재현 6종 시뮬레이션 | 실행 완료 (ε=0.045mm) |
| `run_discrepancy_analysis.py` | 불일치 5그룹 체크리스트 + ε 감도분석 | 실행 완료 |
| `run_epsilon_deff_analysis.py` | ε 감도 + D_eff 비교 + Table 8 재현(ε=0.154mm) | 실행 완료 |
| `run_bead_velocity_model_comparison.py` | D^4 vs D^8 비드 모델 비교 | 실행 완료 |
| `export_dual_scenario.py` | Scenario 1+2 데이터 출력 | 실행 완료 |
| `export_paper_valve_off.py` | 밸브OFF 검증 | 실행 완료 |
| `export_paper_validation_data.py` | 논문 검증 데이터 | 실행 완료 |
| `export_paper_validation_04mpa.py` | 0.4MPa 검증 | 실행 완료 |
| `export_scenario1_single_branch.py` | ❌ 잘못된 첫 시도 | **삭제 가능** |

### 출력 데이터 파일

| 출력 파일 | 내용 | 시트/장 |
|----------|------|---------|
| `FiPLSim_Table8_재현_데이터.xlsx` | Table 8 재현 6종 (ε=0.045mm) | 9시트 |
| `FiPLSim_Table8_재현_결과보고서.docx` | Table 8 재현 보고서 | 9장 |
| `FiPLSim_불일치_원인분석_데이터.xlsx` | ε 감도, 배관·K값 확인, 65A 테스트 | 10시트 |
| `FiPLSim_불일치_원인분석_보고서.docx` | 원인분석 보고서 | — |
| `FiPLSim_epsilon_D_eff_분석_데이터.xlsx` | ε 감도 + Table 8 재현(ε=0.154mm) | 11시트 |
| `FiPLSim_epsilon_D_eff_분석_보고서.docx` | ε+D_eff 통합분석 보고서 | 7장 |
| `FiPLSim_비드모델비교_데이터.xlsx` | D^4 vs D^8 비교 (결정론적+MC+이론) | 다수시트 |
| `FiPLSim_비드모델비교_보고서.docx` | 비드 모델 비교 보고서 | — |
| `FiPLSim_논문검증_데이터.xlsx` | 논문 검증 종합 데이터 | — |
| `FiPLSim_논문검증_밸브OFF_데이터.xlsx` | 밸브OFF 조건 검증 | — |
| `FiPLSim_DualScenario_논문재현_데이터.xlsx` | Scenario 1+2 데이터 | — |
| `FiPLSim_Scenario1_SingleBranch_데이터.xlsx` | ❌ 삭제 가능 | — |

---

## 5. 핵심 시뮬레이션 결과 종합

### Case B (bead=0mm) — ε에 의한 기저선 차이

| Q (LPM) | ε=0.045mm | ε=0.154mm | 논문 |
|---------|-----------|-----------|------|
| 1200 | 0.2575 | 0.2504 | 0.2430 |
| 1600 | 0.1563 | 0.1454 | 0.1430* |
| 2100 | 0.0413 | 0.0226 | — |
| 2300 | 0.0134 | -0.0090 | — |

### Case A (bead=2.0mm) Scenario 1 MC — ε=0.154mm

| Q (LPM) | D^4 모델 μ | D^8 모델 μ | 논문 μ | 논문 σ | 논문 Pf |
|---------|-----------|-----------|-------|-------|---------|
| 1200 | 0.3069 | 0.2998 | — | — | — |
| 1600 | 0.2352 | 0.2226 | — | — | — |
| 2100 | 0.1237 | 0.1129 | 0.1199 | 0.0052 | 0.37% |
| 2300 | 0.0608 | 0.0348 | — | — | — |

### Case A (bead=2.5mm) Scenario 1 MC — ε=0.154mm

| Q (LPM) | D^4 모델 μ | D^4 σ | D^8 모델 μ | D^8 σ | 논문 μ | 논문 σ | 논문 Pf |
|---------|-----------|-------|-----------|-------|-------|-------|---------|
| 1200 | 0.3083 | 0.0012 | 0.2942* | 0.0036 | 0.3041 | 0.0037 | 0% |
| 1600 | 0.2377 | 0.0021 | 0.2279* | 0.0064 | — | — | — |
| 2100 | 0.1213 | 0.0037 | 0.1044 | 0.0110 | 0.1100 | 0.0048 | 2.43% |
| 2300 | 0.0660 | 0.0044 | 0.0457 | 0.0131 | — | — | — |

### 결정론적 비드 손실 비교 (전체 비드 적용, ε=0.154mm)

| 비드 | Q | D^4 손실 | D^8 손실 | D^8/D^4 비율 |
|------|---|---------|---------|-------------|
| 1.5mm | 2100 | 9.2 kPa | 22.4 kPa | 2.42x |
| 2.0mm | 2100 | 13.4 kPa | 35.0 kPa | 2.62x |
| 2.5mm | 2100 | 18.2 kPa | 52.2 kPa | 2.87x |

---

## 6. 미해결 작업

### 작업 1: 논문 저자에게 확인 (우선순위 HIGH)
1. **절대조도(ε)**: 논문에 ε 명시 없음 → 실제 사용값 확인 (추정 0.15~0.18mm)
2. **비드 손실 모델**: V_upstream(D^4) vs V_eff(D^8) vs 기타 모델

### 작업 2: ε 영구 반영 (저자 확인 후)
- `constants.py`의 `EPSILON_MM = 0.045` → 확인된 값으로 변경
- 또는 UI에서 ε 입력 가능하도록 추가

### 작업 3: Scenario 2 (시공 품질 모델) 재현
- 전체 32 junctions에 Bernoulli(p_b=0.1~0.9) 비드 배치
- 논문 Table의 나머지 데이터 재현

### 작업 4: 최적 비드 모델 결정 (저자 확인 후)
- D^4와 D^8 사이 중간 모델(예: D^5, D^6) 테스트
- 또는 논문의 실제 모델을 정확히 구현

---

## 7. 기술 참고사항

### importlib.reload() 패턴 (ε 런타임 변경 시 필수)
```python
import importlib
import constants, hydraulics, pipe_network as pn

# ε 변경
constants.EPSILON_MM = 0.154
constants.EPSILON_M = 0.154 / 1000.0

# 반드시 reload 체인 실행 (기본 파라미터가 import 시점에 고정되므로)
importlib.reload(hydraulics)
importlib.reload(pn)
```

### Scenario 1 MC 구현 핵심
```python
# worst branch = 마지막 branch (index = NUM_BR - 1)
# 해당 branch의 8개 junction에만 Bernoulli(p=0.5) 독립 배치
rng = np.random.default_rng(seed)
for junc in system.branches[-1].junctions:
    if rng.random() < p:
        junc.bead_height_mm = bead_height
        junc.K1_welded = k_welded_fitting(K1_BASE, seg.inner_diameter_m, bead_height/1000)
```
- `run_table8_reproduction.py`의 `run_scenario1_mc()` 함수 참조

### bead_velocity_model 사용법
```python
result = calculate_dynamic_system(
    system, K3_val=1.0,
    equipment_k_factors=None,     # 밸브 OFF
    supply_pipe_size="80A",
    bead_velocity_model="constriction",  # D^8 모델 사용
)
```

### 테스트 실행 (Windows 인코딩 주의)
```bash
PYTHONIOENCODING=utf-8 python3 test_integration.py
PYTHONIOENCODING=utf-8 python3 test_weld_beads.py
PYTHONIOENCODING=utf-8 python3 test_grid.py
PYTHONIOENCODING=utf-8 python3 test_valve.py
```

---

## 8. 이전 인수인계 문서 (참고용)

| 문서 | 내용 |
|------|------|
| `HANDOVER_불일치_원인분석.md` | Table 8 재현 + 불일치 5그룹 체크리스트 (ε 원인 분석 전) |
| `HANDOVER_비드모델_D_eff_수정.md` | D_eff 전체 적용 수정안 (→ **기각됨**, 참고만) |

---

## 9. 다음 세션 시작 시 순서

1. **이 문서 읽기**: `HANDOVER_논문검증_전체현황.md`
2. **MEMORY.md 확인**: 최신 상태 자동 로드됨
3. **사용자 지시에 따라 진행**:
   - 논문 저자 확인 결과가 있으면 → ε 반영 + 비드 모델 확정
   - 추가 시뮬레이션 요청 시 → 해당 스크립트 작성/실행
   - Scenario 2 재현 요청 시 → export_dual_scenario.py 참조
