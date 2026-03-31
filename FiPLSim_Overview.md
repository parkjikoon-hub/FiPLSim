# FiPLSim (Fire Protection Pipe Line Simulator) — 프로그램 개요

> **v4.0** | 최종 업데이트: **2026-03-24** | 작성자: Claude Code

## 변경 이력
| 버전 | 일시 | 주요 변경 |
|------|------|---------|
| v4.0 | 2026-03-24 | K값 NFPA 13 근거 정립, P_REF 통합물성치(0.532723), 양방향 토폴로지, EPANET 검증 반영 |
| v3.0 | 2026-03-18 | WeldBead 제거, 레듀서 Crane TP-410 추가, K2 토글, P_ref 역산 반영 |
| v2.0 | 2026-03-10 | NFTC 103 반영, 비균일 비드 모델 포함 |
| v1.0 | 2026-03-06 | 초기 작성 |

---

## 1. 프로그램 목적

FiPLSim은 **습식 스프링클러 소화배관 시스템의 수리계산 및 통계 분석** 시뮬레이터입니다.

**핵심 비교 대상:**
- **Case A (기존 기술)**: 용접 이음쇠에 내면 비드(돌출)가 존재 → 추가 압력 손실 발생
- **Case B (형상제어 신기술)**: 비드를 제거/제어 → 기본 손실만 발생

**핵심 질문:**
> "용접 비드가 소화배관의 말단 방수압에 얼마나 영향을 미치는가?"
> "어떤 조건에서 비드로 인해 법적 기준(0.1 MPa)을 만족하지 못하는가?"

**v4.0 추가 질문:**
> "단방향(4가지배관)과 양방향(2+2가지배관) 토폴로지에서 비드 영향은 어떻게 달라지는가?"

---

## 2. 이론적 근거 및 수식

### 2.1 주손실 — Darcy-Weisbach 방정식

배관 내 유체가 직관을 흐를 때 발생하는 마찰 손실:

```
h_f = f × (L / D) × (V² / 2g)   [m]
ΔP = ρ × g × h_f / 10⁶          [MPa]
```

| 기호 | 의미 | 단위 |
|------|------|------|
| f | Darcy 마찰계수 (무차원) | - |
| L | 배관 길이 | m |
| D | 배관 내경 | m |
| V | 유속 | m/s |
| g | 중력가속도 (9.81) | m/s² |
| ρ | 유체 밀도 | kg/m³ |

**출처**: Moody, L.F. (1944). "Friction Factors for Pipe Flow." *Trans. ASME*, 66(8), 671-684.

### 2.2 마찰계수 — Colebrook-White 방정식

난류(Re >= 2300) 조건에서 마찰계수를 구하는 반복 수렴식:

```
1/√f = -2.0 × log₁₀[ (ε/D)/3.7 + 2.51/(Re × √f) ]
```

- **층류** (Re < 2300): f = 64 / Re
- **Re** = V × D / ν (레이놀즈 수)
- **ε** = 0.046 mm (탄소강 절대조도, 논문 물성치 기준)
- 초기 추정: Swamee-Jain 근사식, 최대 10회 반복 수렴

**출처**: Colebrook, C.F. (1939). "Turbulent Flow in Pipes." *J. ICE*, 11(4), 133-156.

### 2.3 부차손실 (국부 손실) — K-factor 방법

배관 이음쇠, 밸브, 분기점 등에서 발생하는 국부적 손실:

```
h_m = K × (V² / 2g)   [m]
```

### 2.4 T분기(Tee-Branch) K값 — NFPA 13 기반 산출 (v4.0 개정)

T분기 손실계수는 **NFPA 13 (2019) Table 22.4.3.1.1** "Tee or Cross (Flow Turned 90°)" 등가길이를 Darcy-Weisbach K로 변환하여 산출합니다.

**변환 공식:**
```
K = f_T × (L_eq / D)
f_T = Colebrook-White 완전난류 마찰계수 (ε = 0.046 mm 기준)
```

**관경별 K값 산출표:**

| 관경 | NFPA 13 등가길이 | L_eq/D | f_T | K |
|------|----------------|--------|------|-----|
| 25A | 5 ft (1.524 m) | 56.3 | 0.0220 | **1.24** |
| 32A | 6 ft (1.829 m) | 52.5 | 0.0210 | **1.10** |
| 40A | 8 ft (2.438 m) | 57.7 | 0.0200 | **1.15** |
| 50A | 10 ft (3.048 m) | 58.1 | 0.0195 | **1.13** |
| 65A | 12 ft (3.658 m) | 54.7 | 0.0190 | **1.04** |
| 80A | 15 ft (4.572 m) | 58.7 | 0.0180 | **1.06** |

**교차 검증:**
- Crane TP-410 (K = 60×f_T): 80A → K ≈ 1.08 (NFPA 13과 2% 이내 일치)
- Idelchik (2008) Diagram 7-18: 유량비에 따라 0.88~1.7 범위

**FiPLSim 적용값:**

| 상수 | 값 | 기준 유속 | 산출 근거 |
|------|-----|---------|---------|
| K3 | 1.0 | 65A 가지배관 입구 | NFPA 13 65A 기준 K=1.04의 공학적 단순화 |
| K_TEE_RUN | 0.3 | 80A 교차배관 | Crane TP-410 Tee straight-through |
| K_TEE_BRANCH_80A | 1.06 | 80A 교차배관 | NFPA 13 80A 기준 (양방향 T분기 전용) |

> 상세 산출 과정: `K_TEE_BRANCH_basis.md` 참조

**출처:**
1. NFPA 13 (2019). *Standard for the Installation of Sprinkler Systems*. Table 22.4.3.1.1.
2. Crane Co. (2018). *Technical Paper No. 410*.
3. Idelchik, I.E. (2008). *Handbook of Hydraulic Resistance*. 4th Ed.

### 2.5 K-factor 체계 종합

| 기호 | 의미 | 기본값 | 출처 |
|------|------|--------|------|
| K1_BASE | 배관이음쇠 (비드 0mm 기준) | 0.5 | Borda-Carnot 기반 모델 |
| K2 | 헤드이음쇠 (있음/없음) | 2.5 / 1.4 | Crane TP-410 |
| K3 | 분기 입구 (교차→가지) | 1.0 | NFPA 13, 65A 기준 |
| K_TEE_RUN | 교차배관 직진 | 0.3 | Crane TP-410 |
| K_TEE_BRANCH_80A | 양방향 입구 T분기 | 1.06 | NFPA 13, 80A 기준 |
| 알람밸브 | 습식 | 2.0 | Crane TP-410 |
| 체크밸브 | 스윙형 | 2.0 | Crane TP-410 |
| 게이트밸브 | 전개 | 0.15 | Crane TP-410 |

### 2.6 비드 손실 모델 — K_eff 수식

**핵심 수식:**
```
K_eff = K_base × (D / D_eff)⁴
D_eff = D - 2 × h_b
```

| 기호 | 의미 |
|------|------|
| K_base | 비드 없을 때의 기본 K값 (0.5) |
| D | 원래 배관 내경 (mm) |
| D_eff | 비드로 축소된 유효 내경 (mm) |
| h_b | 비드 높이 (mm) |

**이론적 근거:**
- 연속 방정식: Q = A × V → V ∝ 1/D² (내경 축소 시 유속 증가)
- 손실 ∝ V² → 손실 ∝ 1/D⁴
- **Borda-Carnot 급축소 이론**과 일치 (면적비의 제곱 = 직경비의 4제곱)
- **Idelchik** "Handbook of Hydraulic Resistance" 내부 돌출물 손실과 일치

**비균일 모델:**
- 균일: 모든 비드 동일 높이
- 비균일: 각 접합부마다 정규분포 N(μ, σ²)로 높이 변동
- 4제곱 비선형 수식으로 비균일 시 평균 손실 증가 (젠센 부등식)

**출처**: Idelchik, I.E. (1966). *Handbook of Hydraulic Resistance*. Hemisphere Publishing.

### 2.7 레듀서 국부 손실 — Crane TP-410

가지배관 관경 전환점(65A→50A 등)의 동심 레듀서 K값:

```
Crane TP-410 점진축소 (θ < 45°):  K = 0.8 × sin(θ/2) × (1 - β²)
급축소 이론식:                     K = 0.5 × (1 - β²)
  θ = 레듀서 원뿔 축소 각도 (ASME B16.9 기준)
  β = d2/d1 (하류/상류 직경비)
```

| 구간 | β | θ (도) | K (Crane) |
|------|------|--------|-----------|
| 65A→50A | 0.837 | 8.1 | 0.017 |
| 50A→40A | 0.779 | 9.0 | 0.025 |
| 40A→32A | 0.857 | 5.5 | 0.010 |
| 32A→25A | 0.760 | 9.8 | 0.029 |
| **합계** | | | **0.081** |

**출처**: Crane Co. (2018). *TP-410*; ASME B16.9 (2018).

### 2.8 P_REF 기준압력 — NFPC 103 역산 (v4.0 개정)

시스템 입구 기준압력을 법적 근거에 따라 역산합니다:

```
P_REF = P_terminal_min + ΔP_system_B
```

**v4.0 통합물성치 적용:**

| 항목 | v3.0 (코드 기본) | v4.0 (통합물성치) |
|------|----------------|-----------------|
| ε (조도) | 0.045 mm | **0.046 mm** |
| ρ (밀도) | 998 kg/m³ | **1000 kg/m³** |
| ν (점성계수) | 1.004e-6 m²/s | **1.002e-6 m²/s** |
| ΔP_system_B | 0.4314 MPa | **0.432723 MPa** |
| **P_REF** | **0.5314 MPa** | **0.532723 MPa** |

- 산출 방법: binary search (tol=1e-7, 최대 200회) → 비드 0mm, 32헤드에서 말단 = 0.1000 MPa
- 교차 검증 오차: 0.0% (수리계산 = 시뮬레이션)

### 2.9 설계 유량 산출 근거 (v4.0 추가)

```
Q_total = N_active × Q_head = 32 × 80 = 2,560 LPM
```

| 항목 | 값 | 근거 |
|------|-----|------|
| 헤드당 최소 방수량 (Q_head) | 80 LPM | NFPC 103 규정 |
| 동시 개방 헤드 수 (N_active) | 32개 (4가지 × 8헤드) | 전수 개방 가정 |
| **설계 유량 (Q_total)** | **2,560 LPM** | N_active × Q_head |

> 참고: 실제 소방 설계에서는 개방 헤드 수를 층/용도별로 결정하나, 본 시뮬레이션에서는 최악 조건(전수 개방)을 적용합니다.

### 2.10 Hardy-Cross 반복법 (Full Grid 배관망)

```
보정 유량: ΔQ = -Σ(h_i) / Σ(2|h_i|/Q_i)
수렴 조건: 각 루프의 수두 불균형 < 0.001 m
```

**출처**: Cross, H. (1936). "Analysis of Flow in Networks." *Bulletin No. 286, UIUC*.

---

## 3. 배관 토폴로지

### 3.1 단방향 Tree (기본)

```
                 입구(Riser, 100A)
                      │
           ┌── 밸브/기기류 (K = 6.2) ──┐
           │                            │
      교차배관(Cross Main, 80A)
      ─────┬──────┬──────┬──────┬─────
           B#0    B#1    B#2    B#3(최악)
           │      │      │      │
          65A→50A→40A→32A→25A (각 8헤드)
```

- 가지배관 4개가 **한쪽 방향**으로 배열
- 교차배관 구간: **3개** (B#0→B#3)
- 최악 경로: B#3 (입구에서 가장 먼 가지배관의 말단 헤드)
- 유량 균등 분배: Q_branch = Q_total / 4 = 640 LPM

### 3.2 양방향 Tree (v4.0 추가)

```
      B#1 ─ B#0 ──80A── 입구 ──80A── B#0 ─ B#1
        (좌측 2가지)    T분기     (우측 2가지)
```

- 입구에서 **좌우 대칭** 분기 (2+2 = 총 4가지배관, 32헤드)
- 입구 T분기: K_TEE_BRANCH_80A = 1.06 (NFPA 13, 80A 기준)
- 각 측 유량: Q_total / 2 = 1,280 LPM
- 교차배관 구간: **1개/측** (B#0→B#1)
- 최악 경로: 각 측의 B#1 (말단 가지배관)

**양방향 모델링 방식:**
1. 장비 손실: 전체 유량(Q) 기준 별도 계산 → `calc_equipment_loss_mpa(Q)`
2. T분기 손실: K=1.06, 전체 유량 기준 80A 유속 → `calc_tee_split_loss_mpa(Q)`
3. 유효 입구압 = P_inlet - equipment_loss - tee_split_loss
4. 각 측: `generate_dynamic_system(num_branches=2, total_flow=Q/2)` 호출

### 3.3 Full Grid (Hardy-Cross)

```
      TOP:  T0 ─── T1 ─── T2 ─── T3 ─── T4
     (입구) │      │      │      │      │
            B#1    B#2    B#3    B#4    (연결)
            │      │      │      │      │
      BOT:  B0 ─── B1 ─── B2 ─── B3 ─── B4
```

- 교차배관 2개(TOP/BOT) 평행 배치 → 폐루프
- Hardy-Cross 반복법으로 유량 재분배
- Tree 대비 말단 압력이 3.8배 높음 (비드 영향 미미)

### 3.4 단방향 vs 양방향 비교 결과 (v4.0)

Q=2,560 LPM, P_REF=0.532723 MPa, 비드=0mm 기준:

| 손실 항목 | 단방향 (kPa) | 양방향 (kPa) | 차이 |
|-----------|-------------|-------------|------|
| 교차배관(Cross main) | 39.4 | 2.9 | -36.5 (양방향 유리) |
| T분기(Tee split) | 0.0 | 40.0 | +40.0 (양방향 불리) |
| 배관 마찰(Pipe) | 114.9 | 88.2 | -26.7 (양방향 유리) |
| 이음쇠(Fitting) | 317.9 | 348.1 | +30.2 (양방향 불리) |
| **말단 압력** | **100.0 kPa** | **96.4 kPa** | **-3.6 kPa** |

**결론**: 양방향은 교차배관 경로가 짧아지지만, 입구 T분기 손실(K=1.06)이 이를 상쇄하여 단방향보다 약 3.6 kPa 불리함.

---

## 4. 입력 변수 (상세)

### 4.1 배관망 구조

| 변수명 | 기본값 | 범위 | 설명 |
|--------|--------|------|------|
| num_branches | 4 | 1~200 | 가지배관 총 개수 |
| heads_per_branch | 8 | 1~50 | 가지배관당 스프링클러 헤드 수 |
| branch_spacing_m | 3.5 | 1.0~10.0 | 교차배관 위 가지배관 분기점 간격 (m) |
| head_spacing_m | 2.3 (논문: **2.1**) | 1.7~3.2 | 가지배관 위 헤드 간격 (m) |
| topology | "tree" | tree / grid | 배관망 형태 |
| branch_inlet_config | "80A-50A" (논문: **"80A-65A"**) | 3종 | 분기Tee 입구관 구성 |

### 4.2 운전 조건

| 변수명 | 기본값 | 범위 | 설명 |
|--------|--------|------|------|
| inlet_pressure_mpa | P_REF (0.532723) | 0.1~2.0 | 교차배관 입구 압력 (MPa) |
| total_flow_lpm | N_active × 80 | 100~5000 | 설계 유량 (LPM) |
| active_heads | heads_per_branch | 1~total | 동시 작동 헤드 수 |

> **설계 유량 산출**: Q = N_active × 80 LPM (NFPC 103, 헤드당 최소 방수량)

### 4.3 이음쇠 비드 (Fitting Bead)

| 변수명 | 기본값 | 범위 | 설명 |
|--------|--------|------|------|
| bead_height_mm | 1.5 | 0.1~5.0 | 기존 기술 비드 돌출 높이 (mm) |
| bead_height_std_mm | 0.0 | 0.0~2.0 | 비드 높이 표준편차 (비균일 모델) |
| use_head_fitting | True | True/False | 헤드이음쇠 사용 여부 (K2=2.5/1.4) |
| reducer_mode | "crane" | crane/sudden/fixed/none | 레듀서 손실 모드 |

### 4.4 몬테카를로 시뮬레이션

| 변수명 | 기본값 | 범위 | 설명 |
|--------|--------|------|------|
| mc_iterations | 100 | 10~100,000 | MC 반복 횟수 |
| min_defects | 1 | 0~total | 최소 결함 수 |
| max_defects | 3 | min~total | 최대 결함 수 |
| p_bead | 0.5 | 0.01~0.99 | 각 접합부 비드 존재 확률 (베르누이 MC) |

### 4.5 물성치 (constants.py)

| 상수 | 코드 기본값 | 논문 값 | 단위 |
|------|-----------|--------|------|
| ε (절대 조도) | 0.045 | **0.046** | mm |
| ρ (밀도) | 998 | **1000** | kg/m³ |
| ν (점성계수) | 1.004e-6 | **1.002e-6** | m²/s |
| g (중력가속도) | 9.81 | 9.81 | m/s² |

> **주의**: 논문용 시뮬레이션에서는 코드 기본값이 아닌 위 논문 값을 런타임에 오버라이드합니다.

### 4.6 배관 치수 (JIS/KS Schedule 40)

| 호칭 구경 | 외경 (mm) | 벽두께 (mm) | 내경 (mm) |
|-----------|----------|------------|----------|
| 25A | 33.40 | 3.38 | 26.64 |
| 32A | 42.16 | 3.56 | 35.04 |
| 40A | 48.26 | 3.68 | 40.90 |
| 50A | 60.33 | 3.91 | 52.51 |
| 65A | 73.03 | 5.16 | 62.71 |
| 80A | 88.90 | 5.49 | 77.92 |
| 100A | 114.30 | 6.02 | 102.26 |

---

## 5. 출력 변수

### 5.1 정적 비교 (Case A vs B)

| 출력 | 단위 | 설명 |
|------|------|------|
| terminal_A_mpa | MPa | 기존 기술(A) 최악 말단 압력 |
| terminal_B_mpa | MPa | 신기술(B) 최악 말단 압력 |
| improvement_pct | % | 개선율 = (B-A)/|A| × 100 |
| pass_fail_A / pass_fail_B | PASS/FAIL | 0.1 MPa 기준 판정 |
| loss_pipe_mpa | MPa | 배관 마찰 손실 합계 |
| loss_fitting_mpa | MPa | 이음쇠 기본 손실 합계 |
| loss_bead_mpa | MPa | 비드 추가 손실 합계 |

### 5.2 몬테카를로 결과

| 출력 | 단위 | 설명 |
|------|------|------|
| terminal_pressures | MPa[] | 각 시행의 최악 말단 압력 배열 |
| mean_pressure / std_pressure | MPa | 평균 / 표준편차 |
| p_below_threshold (Pf) | 0~1 | 기준 미달 확률 (0.1 MPa) |
| fail_rate_CI95 | [low, high] | Wilson 95% 신뢰구간 |

### 5.3 민감도 / 파라미터 스캐닝

| 출력 | 설명 |
|------|------|
| single_bead_pressures | 각 위치 단독 비드 시 압력 배열 |
| ranking | 영향도 순위 (1위 = 가장 치명적) |
| critical_inlet_mpa | 비드높이별 임계 입구압 (binary search) |

### 5.4 양방향 비교 출력 (v4.0 추가)

| 출력 | 설명 |
|------|------|
| tee_split_loss_mpa | T분기 손실 (양방향만 해당) |
| equipment_loss_mpa | 장비류 손실 (전체 유량 기준) |
| cross_main_loss_mpa | 교차배관 누적 손실 |
| p_reduction_vs_uni_mpa | 양방향의 uni 대비 압력 절감량 |

---

## 6. 법적 기준 (NFPC 103 + NFTC 103)

### 6.1 성능기준 (NFPC 103)

| 항목 | 기준값 |
|------|--------|
| 최소 방수압 | 0.1 MPa |
| 최대 방수압 | 1.2 MPa |
| 헤드당 최소 방수량 | 80 L/min |

### 6.2 기술기준 (NFTC 103)

| 항목 | 기준값 |
|------|--------|
| 가지배관 최대 유속 | 6 m/s |
| 교차배관/기타 최대 유속 | 10 m/s |

### 6.3 관경 자동 선정 (NFTC 103 표 2.5.3.3)

**가지배관:** 1~2헤드=25A, 3=32A, 4~5=40A, 6~10=50A, 11+=65A
**교차배관:** ~30헤드=65A, 31~60=80A, 61~100=100A

---

## 7. 프로그램 검증 (Validation)

### 7.1 이중 검증 체계

| 경로 | 방법 | 결과 |
|------|------|------|
| 수리계산 (Analytical) | 순수 수식 직접 계산 | ΔP = 0.432723 MPa |
| 시뮬레이션 (Simulation) | 배관망 객체 순회 계산 | ΔP = 0.432723 MPa |
| **교차 검증 오차** | | **0.0%** |
| **입구 압력 독립성** | | **0.0%** |

### 7.2 EPANET 독립 검증 (v4.0 추가)

EPyT v2.3.5 / EPANET 2.2로 9개 케이스를 독립 검증:

| 지표 | 결과 |
|------|------|
| 검증 케이스 수 | 9건 |
| 최대 오차 | 0.936% |
| 평균 오차 | 0.239% |
| 최악 분기 일치율 | 100% |
| **판정** | **ALL PASS** |

### 7.3 테스트 수트

| 테스트 파일 | 수 | 검증 대상 |
|------------|---|---------|
| test_grid.py | 46 | Grid 배관망, Hardy-Cross |
| test_integration.py | 48 | 통합 테스트 |
| test_valve.py | 63 | 밸브, 레듀서, K2 토글, 3항 분리 |
| **합계** | **157** | |

---

## 8. 시뮬레이션 가능 항목

### 8.1 기본 분석

| 기능 | 설명 | UI 탭 |
|------|------|-------|
| Case A vs B 비교 | 기존 기술 vs 신기술 말단 압력 비교 | Tab 1 |
| 압력 프로파일 | 최악 가지배관 구간별 누적 압력 그래프 | Tab 1 |
| 3항 손실 분리 | 배관마찰 / 이음쇠 / 비드 손실 분리 | Tab 1 |
| P-Q 곡선 분석 | 펌프 성능곡선-시스템 저항곡선 교점 | Tab 2 |

### 8.2 확률론적 분석 (몬테카를로)

| 기능 | 설명 | UI 탭 |
|------|------|-------|
| 기존 MC | 결함 1~3개 무작위 배치 + 비드 높이 | Tab 3 |
| 베르누이 MC | 각 접합부 독립 확률 p로 비드 결정 | Tab 7 |
| 실패 확률 (Pf) | P(말단 < 0.1 MPa) 산출 + 95% CI | Tab 3, 7 |

### 8.3 민감도 / 파라미터 스캐닝

| 기능 | 설명 | UI 탭 |
|------|------|-------|
| 단일 비드 민감도 | 각 헤드 위치별 영향도 순위 | Tab 4 |
| 연속변수 스캐닝 | 입구압, 유량, 비드높이 등 연속 변화 | Tab 6 |
| 2인자 실험계획법 | p_bead × h_b → Pf(%) 2D 히트맵 | Tab 8 |

### 8.4 배치 시뮬레이션 스크립트

| 스크립트 | 용도 | 결과 위치 |
|---------|------|---------|
| run_resim.py | 통합물성치 P_REF 산출 + D1b/D2/S1/F 캠페인 | resim_results/ |
| run_sim_v2.py | 32헤드 고정 + 입구압/비드 스윕 10케이스 | 2nd sim_results/ |
| run_sim_v3.py | 전이구간 보강 S2b/P1b/D1b | 3rd sim_results/ |
| run_uni_bi_compare.py | 단방향 vs 양방향 3캠페인 | uni_bi_branch_sim_compare/ |
| run_epanet_validation.py | EPANET 독립 검증 9케이스 | epanet_validation/ |

---

## 9. 프로그램 구조

### 9.1 파일 구성

```
FiPLSim/
├── [핵심 프로그램]
│   ├── constants.py      물리 상수, 배관 치수, K값 (NFPA 13 근거 포함)
│   ├── hydraulics.py     유체역학 계산 (Darcy, Colebrook, K_eff)
│   ├── pipe_network.py   동적 배관망 생성 및 압력 계산
│   ├── hardy_cross.py    Full Grid 배관망 (Hardy-Cross 솔버)
│   ├── simulation.py     MC 시뮬레이션, 민감도, 변수 스캔
│   ├── pump.py           펌프 P-Q 곡선, 운전점, 에너지 절감
│   └── app.py            Streamlit UI (8탭 대시보드)
│
├── [테스트]
│   ├── test_integration.py   48개 통합 테스트
│   ├── test_grid.py          46개 Grid 배관망 테스트
│   └── test_valve.py         63개 밸브/기기류 테스트
│
├── [문서 / 근거 자료]
│   ├── FiPLSim_Overview.md        ← 이 파일
│   ├── FiPLSim_Design_Manual.md   설계 매뉴얼
│   └── K_TEE_BRANCH_basis.md      T분기 K값 NFPA 13 산출 근거
│
└── [논문용 시뮬레이션 스크립트]
    ├── run_resim.py               통합물성치 재시뮬레이션
    ├── run_sim_v2.py              2nd 배치 (입구압/비드 스윕)
    ├── run_sim_v3.py              3rd 배치 (전이구간 보강)
    ├── run_uni_bi_compare.py      단방향 vs 양방향 비교
    └── run_epanet_validation.py   EPANET 독립 검증
```

### 9.2 압력 계산 경로 (최악 가지배관)

```
입구 압력 P_inlet
  │
  ├─ [-] 밸브/기기류 손실 (라이저)
  ├─ [-] [양방향만] T분기 손실 (K=1.06, 80A 전체 유량)
  │
  ├─ [-] 교차배관 직관 마찰 × (n-1)구간
  ├─ [-] Tee-Run 손실 × (n-1)개 (K=0.3)
  │
  ├─ [-] 분기 입구 손실 K3 (=1.0, 65A 유속)
  ├─ [-] 입구관 마찰 (0.3m, 65A)
  │
  └─ 가지배관 순환 (m개 헤드)
       for h = 0 to m-1:
         ├─ [-] 직관 마찰 (Darcy-Weisbach)
         ├─ [-] 이음쇠 비드 K1 (K_eff = K_base × (D/D_eff)⁴)
         ├─ [-] 헤드 K2 (2.5 또는 1.4)
         └─ [-] 레듀서 (관경 전환 시, Crane TP-410)
       │
       ▼
  말단 압력 P_terminal  ← 0.1 MPa 이상이면 PASS
```

---

## 10. 핵심 시뮬레이션 결과 (v4.0 기준)

### 10.1 기본 결과 (통합물성치)

| 항목 | 값 |
|------|-----|
| **P_REF** | **0.532723 MPa** (NFPC 103 역산, 통합물성치) |
| Case B @P_REF 말단 압력 | 0.10000 MPa (PASS 경계) |
| Case A @P_REF 말단 압력 | 0.08626 MPa (FAIL) |
| 비드 추가 손실 (1.5mm) | 0.01374 MPa (13.7 kPa) |
| EPANET 교차 검증 오차 | 최대 0.936%, 평균 0.239% |

### 10.2 양방향 vs 단방향 비교 (Q=2560, P_REF)

| 항목 | 단방향(4가지) | 양방향(2+2) |
|------|-------------|-----------|
| 말단 압력 (bead=0) | 0.10000 MPa | 0.09644 MPa |
| PASS/FAIL | PASS (경계) | FAIL (-3.6 kPa) |
| 임계 입구압 (bead=0) | 0.53281 MPa | 0.53633 MPa |
| **양방향 추가 필요 압력** | — | **+3.5 kPa** |

---

## 11. 참고 문헌

| # | 출처 | 적용 위치 |
|---|------|---------|
| 1 | NFPA 13 (2019). *Standard for the Installation of Sprinkler Systems*. Table 22.4.3.1.1. | **T분기 K값 (K3, K_TEE_BRANCH_80A)** |
| 2 | Crane Co. (2018). *Technical Paper No. 410*. | K-factor 전체, K2, K_TEE_RUN, 레듀서 K |
| 3 | Idelchik, I.E. (2008). *Handbook of Hydraulic Resistance*. 4th Ed. | Borda-Carnot K_eff, T분기 교차검증 |
| 4 | ASME B16.9 (2018). *Factory-Made Wrought Buttwelding Fittings*. | 레듀서 원뿔 각도 (θ) |
| 5 | Moody, L.F. (1944). "Friction Factors for Pipe Flow." *Trans. ASME*, 66(8). | Darcy-Weisbach 주손실 |
| 6 | Colebrook, C.F. (1939). "Turbulent Flow in Pipes." *J. ICE*, 11(4). | 마찰계수 |
| 7 | Cross, H. (1936). "Analysis of Flow in Networks." *Bulletin No. 286, UIUC*. | Hardy-Cross |
| 8 | NFPC 103 (소방시설의 화재안전성능기준). | 말단 방수압 0.1~1.2 MPa, 방수량 80 LPM |
| 9 | NFTC 103 (소방시설의 기술기준). | 배관 구경 선정, 유속 제한 |
| 10 | JIS G 3452 / KS D 3507. *Carbon Steel Pipes for Ordinary Piping*. | Schedule 40 배관 치수 |

---

## 12. 모델 한계점 (Limitations)

1. **환상 비드 가정**: 실제 비드는 부분적·불규칙하나, 완전 환형으로 가정 (보수적 추정)
2. **K3 단순화**: NFPA 13 기준 65A K=1.04를 1.0으로 단순화 (~4% 차이)
3. **양방향 T분기**: NFPA 13 등가길이 기반 K=1.06 적용. 실제 분기 형상(Y자, 대칭헤더 등)에 따라 K값 변동 가능
4. **균등 유량 가정**: Tree 모드에서 각 헤드에 동일 유량 배분. Grid 모드는 Hardy-Cross로 유량 균형
5. **온도 영향**: 20°C 기준 물성치 사용. 온도 변화 미반영
6. **필드 검증**: EPANET 검증은 완료. 실험실/현장 실측 데이터 교차 검증은 향후 과제

---

## 13. 실행 방법

```bash
# UI 실행
streamlit run app.py

# 테스트 (157개)
python3 test_grid.py && python3 test_integration.py && python3 test_valve.py

# 논문용 시뮬레이션 (통합물성치)
PYTHONIOENCODING=utf-8 python3 run_resim.py
PYTHONIOENCODING=utf-8 python3 run_uni_bi_compare.py

# EPANET 독립 검증
PYTHONIOENCODING=utf-8 python3 run_epanet_validation.py
```
