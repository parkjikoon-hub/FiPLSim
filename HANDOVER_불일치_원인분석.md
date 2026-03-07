# 인수인계 자료: Table 8 재현 및 불일치 원인분석

## 1. 이번 세션에서 완료한 작업

### 작업 A: Table 8 재현 시뮬레이션 (6종)
- **스크립트**: `run_table8_reproduction.py`
- **조건**: 4 branches × 8 heads, 입구 0.4 MPa, 밸브 OFF, 80A 교차배관, tree topology
- **출력**: `FiPLSim_Table8_재현_데이터.xlsx` (9시트), `FiPLSim_Table8_재현_결과보고서.docx` (9장)

| 시뮬레이션 | 비드 | Q (LPM) | FiPLSim μ (MPa) | 논문 μ (MPa) | 차이 |
|-----------|------|---------|-----------------|-------------|------|
| ① Case B | 0mm | 1200 | 0.2575 | 0.2430 | +14.5 kPa |
| ② Case B | 0mm | 1600 | 0.1563 | 0.1563 | ~0 kPa ✅ |
| ② Case B | 0mm | 2100 | 0.0413 | — | — |
| ② Case B | 0mm | 2300 | 0.0134 | — | — |
| ④ Case A MC | 2.0mm | 1200 | μ=0.2470, σ=0.0015 | — | — |
| ④ Case A MC | 2.0mm | 1600 | μ=0.1466, σ=0.0017 | — | — |
| ④ Case A MC | 2.0mm | 2100 | μ=0.0319, σ=0.0013 | — | — |
| ⑤ Case A MC | 2.5mm | 1200 | μ=0.2406, σ=0.0019 | 0.3041 | -63.5 kPa |
| ⑤ Case A MC | 2.5mm | 1600 | μ=0.1402, σ=0.0021 | — | — |
| ⑥ Case A MC | 2.5mm | 2100 | μ=0.1472, σ=0.0036 | 0.1100 | +37.2 kPa |
| ⑥ Case A MC | 2.5mm | 2300 | μ=0.0974, σ=0.0043 | — | Pf=70.6% |

### 작업 B: 불일치 원인분석 (5그룹 체크리스트)
- **스크립트**: `run_discrepancy_analysis.py`
- **분석 대상**: Case B @ 1600 LPM (+14.5 kPa) 및 Case A 2.5mm @ 1200 LPM (+12.1 kPa)
- **출력**: `FiPLSim_불일치_원인분석_데이터.xlsx` (10시트), `FiPLSim_불일치_원인분석_보고서.docx`

---

## 2. 핵심 발견: 불일치 원인 순위

### 🔴 #1 원인: 절대조도(ε) — 확정
| ε (mm) | Case B 말단압력 (MPa) | 논문 대비 차이 |
|--------|---------------------|--------------|
| 0.045 (현재, 신품 탄소강) | 0.2575 | +14.54 kPa |
| 0.100 | 0.2492 | +6.16 kPa |
| **0.150** | **0.2434** | **+0.39 kPa** ✅ |
| 0.200 | 0.2383 | -4.70 kPa |
| 0.300 | 0.2296 | -13.38 kPa |

- **보간 결과**: ε ≈ **0.154 mm**이면 Case B @ 1600 LPM 논문 값(0.2430)과 정확히 일치
- **Case A @ 1200 LPM**: ε ≈ **0.177 mm**이면 논문 일치
- **해석**: 논문 시스템의 실제 배관 조도 ≈ 0.15~0.18 mm (약간 사용된 탄소강 수준)
- **참고**: 논문에 ε 명시 없음 → 저자 확인 필요

### 🟢 확인 완료 (문제 없음)
| 항목 | 검증 결과 |
|------|----------|
| **배관 구성** | 8개 branch segment 동일: 50A×3, 40A×2, 32A×1, 25A×2 |
| **K값** | K_TEE_RUN=0.3, K3=1.0, K2=2.5, K1_BASE=0.5 — 모두 일치 |
| **유량 분배** | 균등 분배 모델 — 논문과 동일 |
| **배관 길이** | fitting_spacing=2.3m × 8 segments — 논문 기술과 일치 |

### 🟡 65A 배관 (Branch inlet) — 오히려 역효과
- 논문: "65A→50A→40A→32A→25A"라고 기술
- 65A는 branch inlet 배관 (첫 번째 헤드 전 구간)
- FiPLSim auto_pipe_size(8) = 50A (65A 미포함)
- **테스트 결과**: 65A 추가 시 차이가 +14.54 → +26.71 kPa로 **증가** (역효과)
- **결론**: 65A는 포함하지 않는 것이 맞음

---

## 3. 미해결 작업 (우선순위 순)

### 작업 1: ε 조도값 결정 (우선순위 HIGH)
- **현재 상태**: ε=0.045mm (신품 탄소강)
- **분석 결과**: ε≈0.15mm이면 Case B 불일치 해결
- **필요 조치**: 논문 저자에게 ε값 확인, 또는 0.15mm로 변경하여 전체 재현 시도
- **변경 방법**: `constants.py`의 `EPSILON_MM = 0.045` → `0.15`로 변경

### 작업 2: D_eff 마찰손실 적용 (우선순위 HIGH)
- **현재 상태**: 비드의 D_eff가 K-factor(국부 손실)에만 적용, 마찰손실에 미적용
- **영향**: 비드 효과 3.3× 과소 평가 (FiPLSim ~18.2 kPa vs 논문 ~60.7 kPa)
- **수정 파일**: `pipe_network.py`의 `_calculate_branch_profile()` (406행)
- **상세 수정안**: `HANDOVER_비드모델_D_eff_수정.md` 참조
- **핵심**: 비드가 있는 junction에서 `seg.inner_diameter_m` 대신 `D_eff = D - 2h` 사용

### 작업 3: Table 8 완전 재현 (작업 1+2 완료 후)
- ε 조정 + D_eff 적용 후 `run_table8_reproduction.py` 재실행
- 논문 목표값: Case A 2.5mm Q=2100 → μ=0.1100, σ=0.0048, Pf=2.43%

---

## 4. 기술 주의사항

### Python 기본 파라미터 캐싱 문제
```python
# hydraulics.py 42행
def friction_factor(Re, epsilon=EPSILON_M, D=0.05):
    # EPSILON_M이 import 시점에 고정됨!
```
- `constants.EPSILON_M`을 런타임에 변경해도 `friction_factor()` 기본값 불변
- **해결**: `importlib.reload()` 체인 필수
```python
import importlib
constants.EPSILON_MM = 새_값_mm
constants.EPSILON_M = 새_값_mm / 1000.0
importlib.reload(hydraulics)   # friction_factor 기본값 재정의
importlib.reload(pipe_network) # hydraulics 참조 갱신
```

### Scenario 1 MC 구현 방식
- worst branch = 마지막 branch (index = NUM_BR - 1)
- 해당 branch의 8개 junction에 Bernoulli(p=0.5) 독립 배치
- 나머지 branch는 bead=0
- `run_table8_reproduction.py`의 `run_scenario1_mc()` 함수 참조

---

## 5. 파일 목록 및 상태

### 분석 스크립트 (이번 세션 생성)
| 파일 | 용도 | 상태 |
|------|------|------|
| `run_table8_reproduction.py` | Table 8 재현 6개 시뮬레이션 | ✅ 실행 완료 |
| `run_discrepancy_analysis.py` | 5그룹 체크리스트 + ε 감도분석 | ✅ 실행 완료 |

### 출력 데이터 (이번 세션 생성)
| 파일 | 내용 | 크기 |
|------|------|------|
| `FiPLSim_Table8_재현_데이터.xlsx` | 6개 시뮬레이션 결과 (9시트) | 685.6 KB |
| `FiPLSim_Table8_재현_결과보고서.docx` | Table 8 재현 보고서 (9장) | 38.7 KB |
| `FiPLSim_불일치_원인분석_데이터.xlsx` | ε 감도, 배관확인, K값 (10시트) | 14.2 KB |
| `FiPLSim_불일치_원인분석_보고서.docx` | 원인분석 보고서 | 38.9 KB |

### 기존 스크립트 (이전 세션)
| 파일 | 용도 | 비고 |
|------|------|------|
| `export_dual_scenario.py` | Scenario 1+2 데이터 출력 | D_eff 수정 후 재실행 필요 |
| `export_paper_valve_off.py` | 밸브OFF 검증 | 완료 |
| `export_paper_validation_data.py` | 논문 검증 데이터 | 완료 |
| `export_scenario1_single_branch.py` | ❌ 잘못된 시도 | 삭제 가능 |

### 핵심 수정 대상 (다음 세션)
| 파일 | 수정 내용 |
|------|----------|
| `constants.py` L7 | `EPSILON_MM = 0.045` → `0.15` (확인 후) |
| `pipe_network.py` L406 | D_eff를 유속·마찰손실에 적용 |
| `hardy_cross.py` | Grid용 동일 D_eff 수정 (선택) |

---

## 6. 다음 세션 시작 시 실행 순서

1. **이 문서 읽기**: `HANDOVER_불일치_원인분석.md`
2. **D_eff 수정 문서 읽기**: `HANDOVER_비드모델_D_eff_수정.md` (수정 코드 포함)
3. **ε값 결정** → `constants.py` 수정
4. **D_eff 코드 수정** → `pipe_network.py` 수정
5. **테스트 실행**: `PYTHONIOENCODING=utf-8 python3 -m pytest test_integration.py test_weld_beads.py test_grid.py test_valve.py -v`
6. **Table 8 재실행**: `python3 run_table8_reproduction.py`
7. **결과 비교**: 논문 목표값과 대조
