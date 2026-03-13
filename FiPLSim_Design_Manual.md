# FiPLSim 설계 매뉴얼 (Design Manual)

> **v1.0** | 최종 업데이트: **2026-03-13** | 작성자: Claude Code

## 변경 이력

| 버전 | 일시 | 주요 변경 |
|------|------|---------|
| v1.0 | 2026-03-13 | 초기 작성 — 설계변경(v3.0) 반영 완료 상태 기준 |

---

## 1. 프로그램 개요

**FiPLSim** (Fire Protection Pipe Loss Simulator)은 습식 스프링클러 소화배관의 수리학적 손실을 시뮬레이션하는 프로그램입니다.

### 1.1 목적
- **기존 용접 기술**(이음쇠 내부 비드 돌출)과 **형상제어 신기술**(비드 없음) 간의 수리학적 성능 차이를 정량적으로 비교
- 비드 높이에 따른 압력 손실 증가를 이론적으로 분석하고, 말단 스프링클러 헤드의 방수압 Pass/Fail 판정 수행
- 몬테카를로 시뮬레이션을 통한 확률론적 분석 제공

### 1.2 기술 스택
- **언어**: Python 3.14
- **UI**: Streamlit (웹 기반 대시보드)
- **수치 계산**: NumPy, SciPy (보간/루트파인딩)
- **시각화**: Plotly
- **보고서**: python-docx (DOCX), openpyxl (XLSX)
- **실행 명령**: `streamlit run app.py`

### 1.3 파일 구조

| 파일 | 역할 |
|------|------|
| `constants.py` | 물리상수, 배관치수, K값, 분기구조, 펌프DB |
| `hydraulics.py` | Darcy-Weisbach, Colebrook-White, K_eff, 레듀서 K |
| `pipe_network.py` | 동적 배관망 생성/계산, 3항 분리, 수리계산 역산 |
| `hardy_cross.py` | Full Grid 배관망 (Hardy-Cross 반복 솔버) |
| `pump.py` | P-Q 곡선 보간, 운전점 탐색, 에너지 절감 분석 |
| `simulation.py` | 몬테카를로(MC), 민감도, 스윕, 2인자 분석 |
| `app.py` | Streamlit UI (8탭 대시보드) |
| `run_paper_simulations.py` | 논문용 배치 자동화 (Stage 1~4) |

---

## 2. 소화배관 전체 구조

### 2.1 배관 토폴로지 (Topology)

FiPLSim은 두 가지 배관망 토폴로지를 지원합니다:

#### Tree 모드 (기본)
```
                    입구(Riser, 100A)
                         │
              ┌── 밸브/기기류 (K_total = 6.2) ──┐
              │                                  │
         교차배관(Cross Main, 80A)
         ─────┬──────┬──────┬──────┬─────
              B#1    B#2    B#3    B#4(최악)
              │      │      │      │
             50A    50A    50A    50A  ← 헤드 1~3
             40A    40A    40A    40A  ← 헤드 4~5
             32A    32A    32A    32A  ← 헤드 6
             25A    25A    25A    25A  ← 헤드 7~8(말단)
```
- 유량은 입구에서 각 가지배관으로 **균등 분배** (Q_branch = Q_total / N)
- 최악 경로(Worst Path): 입구에서 가장 먼 B#4의 말단 헤드

#### Grid 모드 (Hardy-Cross)
```
         TOP:  T0 ─── T1 ─── T2 ─── T3 ─── T4
         (입구) │      │      │      │      │
               B#1    B#2    B#3    B#4    (연결)
               │      │      │      │      │
         BOT:  B0 ─── B1 ─── B2 ─── B3 ─── B4
```
- TOP/BOT 양방향 교차배관 + 양끝 수직 연결배관 = 폐루프(Closed Loop)
- Hardy-Cross 반복법으로 각 배관의 유량 분배를 수렴 계산
- 유량 재분배에 의해 Tree 모드 대비 말단 압력이 다소 높음

### 2.2 확정 배관 구조: 80A-65A

현재 프로그램의 **기본 배관 구조**는 `80A-65A` 설정입니다:

```
교차배관(80A) ─[분기Tee]─ 65A(입구관 0.3m) ─[레듀서 65A→50A]─ 50A(헤드×3) ─ 40A(×2) ─ 32A(×1) ─ 25A(×2)
```

| 구간 | 호칭 구경 | 내경 (mm) | 담당 헤드 수 | 비고 |
|------|-----------|-----------|-------------|------|
| 교차배관 | 80A | 77.92 | 32 (전체) | NFTC 103 기준 자동 선정 |
| 입구관 | 65A | 62.71 | — | 분기Tee 중심~레듀서, 0.3m |
| 헤드 1~3 | 50A | 52.51 | 8~6 | 레듀서 65A→50A 후 |
| 헤드 4~5 | 40A | 40.90 | 5~4 | 레듀서 50A→40A |
| 헤드 6 | 32A | 35.04 | 3 | 레듀서 40A→32A |
| 헤드 7~8 | 25A | 26.64 | 2~1 | 레듀서 32A→25A (말단) |

- **4 branches × 8 heads = 32 junctions** (총 스프링클러 헤드)
- **B#4 = 최악 경로** (입구에서 가장 먼 가지배관)

### 2.3 자동 관경 선정 규칙

**NFTC 103 (소화설비 기술기준) 표 2.5.3.3 "가"란 기준:**

| 하류 헤드 수 | 가지배관 구경 |
|-------------|--------------|
| 11개 이상 | 65A |
| 6~10개 | 50A |
| 4~5개 | 40A |
| 3개 | 32A |
| 1~2개 | 25A |

**교차배관 구경:**

| 담당 헤드 수 | 교차배관 구경 |
|-------------|--------------|
| 61개 이상 | 100A |
| 31~60개 | 80A |
| 30개 이하 | 65A |

### 2.4 배관 치수 테이블

**JIS/KS Schedule 40 탄소강 강관:**

| 호칭 | 외경 (mm) | 관벽 두께 (mm) | 내경 (mm) |
|------|-----------|---------------|-----------|
| 25A | 33.40 | 3.38 | 26.64 |
| 32A | 42.16 | 3.56 | 35.04 |
| 40A | 48.26 | 3.68 | 40.90 |
| 50A | 60.33 | 3.91 | 52.51 |
| 65A | 73.03 | 5.16 | 62.71 |
| 80A | 88.90 | 5.49 | 77.92 |
| 100A | 114.30 | 6.02 | 102.26 |

### 2.5 밸브/기기류 (수직 라이저)

공급배관(100A 라이저)에 설치된 밸브류와 K-factor:

| 기기명 | K값 | 수량 | 소계 K | 출처 |
|--------|-----|------|--------|------|
| 알람밸브 (습식) | 2.0 | 1 | 2.0 | Crane TP-410 |
| 유수검지장치 | 1.0 | 1 | 1.0 | 제조사 카탈로그 |
| 게이트밸브 (전개) | 0.15 | 2 | 0.3 | Crane TP-410 |
| 체크밸브 (스윙형) | 2.0 | 1 | 2.0 | Crane TP-410 |
| 90도 엘보 | 0.75 | 1 | 0.75 | Crane TP-410 |
| 리듀서 (점축소) | 0.15 | 1 | 0.15 | Crane TP-410 |
| **합계** | | | **6.2** | |

---

## 3. 학술적/이론적 근거

### 3.1 주손실 (Major Loss) — Darcy-Weisbach 방정식

배관 내 마찰에 의한 직관 손실을 계산합니다.

$$h_f = f \times \frac{L}{D} \times \frac{V^2}{2g}$$

| 기호 | 의미 | 단위 |
|------|------|------|
| h_f | 마찰 손실 수두 | m |
| f | Darcy 마찰계수 | 무차원 |
| L | 배관 길이 | m |
| D | 배관 내경 | m |
| V | 유속 | m/s |
| g | 중력가속도 (9.81) | m/s^2 |

**출처**: Moody, L.F. (1944). "Friction Factors for Pipe Flow." *Transactions of the ASME*, 66(8), 671-684.

### 3.2 마찰계수 — Colebrook-White 방정식

난류 영역의 Darcy 마찰계수를 반복법으로 구합니다.

$$\frac{1}{\sqrt{f}} = -2.0 \times \log_{10}\left(\frac{\varepsilon/D}{3.7} + \frac{2.51}{Re \times \sqrt{f}}\right)$$

| 영역 | 조건 | 마찰계수 |
|------|------|---------|
| 층류 | Re < 2,300 | f = 64/Re |
| 난류 | Re >= 2,300 | Colebrook-White 반복법 (초기값: Swamee-Jain 근사) |

- **절대 조도**: epsilon = 0.045 mm (탄소강, 신관)
- **수렴 조건**: 상대 오차 < 10^-8, 최대 10회 반복

**출처**: Colebrook, C.F. (1939). "Turbulent Flow in Pipes, with Particular Reference to the Transition Region between the Smooth and Rough Pipe Laws." *Journal of the Institution of Civil Engineers*, 11(4), 133-156.

### 3.3 부차 손실 (Minor Loss) — K-factor 방법

이음쇠, 밸브, 분기점 등의 국부 손실을 K계수로 계산합니다.

$$h_m = K \times \frac{V^2}{2g}$$

FiPLSim에서 사용하는 K값 체계:

| 기호 | 의미 | 기본값 | 출처 |
|------|------|--------|------|
| K1 | 배관이음쇠 (비드 영향 포함) | 0.5 (비드 0mm 기준) | Borda-Carnot 기반 모델 |
| K2 | 헤드이음쇠 (분기/방향전환) | 2.5 (있음) / 1.4 (없음) | Crane TP-410 |
| K3 | 분기 입구 (교차→가지 분기) | 1.0 | Tee-Branch flow |
| K_TEE_RUN | 교차배관 직진 (분기 후 직진) | 0.3 | Crane TP-410 |

### 3.4 비드 높이 모델 — Borda-Carnot 기반 K_eff

용접 이음쇠 내부의 비드 돌출이 유효 내경을 축소시켜 K값을 증가시키는 모델입니다.

$$K_1 = K_{base} \times \left(\frac{D}{D_{eff}}\right)^4$$

$$D_{eff} = D - 2 \times h_b$$

| 기호 | 의미 |
|------|------|
| K_base | 비드 없을 때 기본 K값 (0.5) |
| D | 배관 내경 (mm) |
| D_eff | 유효 내경 (mm) — 비드 양측 돌출 가정 |
| h_b | 비드 돌출 높이 (mm) |

**비드 높이별 K1값 (50A 기준, D=52.51mm):**

| 비드 높이 (mm) | D_eff (mm) | K1 | K1 증가율 |
|---------------|-----------|-----|----------|
| 0.0 (신기술) | 52.51 | 0.500 | 기준 |
| 0.5 | 51.51 | 0.540 | +8.0% |
| 1.0 | 50.51 | 0.584 | +16.8% |
| 1.5 (기존기술) | 49.51 | 0.633 | +26.5% |
| 2.0 | 48.51 | 0.687 | +37.3% |

**비균일 모델**: `bead_height_std_mm > 0`인 경우, 각 접합부의 비드 높이를 정규분포 N(mu, sigma)에서 독립 샘플링하여 비균일성을 반영합니다.

**출처**: Borda-Carnot 급축소/급확대 이론의 일반화. Idelchik, I.E. (1966). *Handbook of Hydraulic Resistance*.

### 3.5 K2 헤드이음쇠 토글

스프링클러 헤드의 연결 구조에 따라 K2값이 달라집니다:

| 모드 | K2 값 | 구조 | 근거 |
|------|-------|------|------|
| 헤드이음쇠 있음 (기본) | 2.5 | 배관이음쇠 + 헤드이음쇠 + 헤드 | T분기 1.4 + 헤드이음쇠 고유 저항 1.1 |
| 헤드이음쇠 없음 | 1.4 | 배관이음쇠 + 헤드 직접 연결 | Crane TP-410 (K = 60 * fT, Tee branch flow) |

**출처**: Crane Technical Paper 410, "Flow of Fluids Through Valves, Fittings, and Pipe" — Table A-28, Tee 분기 흐름 K = 60 * fT, fT = 0.023 (50A) ~ 0.026 (25A), K = 1.38 ~ 1.56 (대표값 1.4 적용).

### 3.6 레듀서 국부 손실 — Crane TP-410

가지배관의 관경 전환점(65A->50A, 50A->40A 등)에 설치된 동심 레듀서의 K값:

#### 계산식

**Crane TP-410 점진축소 (theta < 45도):**
$$K = 0.8 \times \sin(\theta/2) \times (1 - \beta^2)$$

**급축소 이론식:**
$$K = 0.5 \times (1 - \beta^2)$$

| 기호 | 의미 |
|------|------|
| theta | 레듀서 원뿔 축소 각도 (도) — ASME B16.9 치수로 산출 |
| beta | 직경비 = d2/d1 (하류/상류) |

#### 레듀서 K값 계산 결과

| 구간 | d1 (mm) | d2 (mm) | beta | theta (도) | K (Crane) | K (급축소) |
|------|---------|---------|------|-----------|-----------|-----------|
| 65A->50A | 62.71 | 52.51 | 0.837 | 8.1 | 0.017 | 0.149 |
| 50A->40A | 52.51 | 40.90 | 0.779 | 9.0 | 0.025 | 0.197 |
| 40A->32A | 40.90 | 35.04 | 0.857 | 5.5 | 0.010 | 0.133 |
| 32A->25A | 35.04 | 26.64 | 0.760 | 9.8 | 0.029 | 0.211 |
| **합계** | | | | | **0.081** | **0.690** |

#### 레듀서 손실 4가지 모드

| 모드 | 설명 | 사용 시기 |
|------|------|---------|
| `crane` (기본) | Crane TP-410 점진축소식 | 정밀 분석 (권장) |
| `sudden` | 급축소 이론식 | 보수적 평가 |
| `fixed` | 사용자 지정 고정값 (K=0.05) | Bentley HAMMER 등 참조 |
| `none` | 레듀서 손실 무시 | NFPA 13 관행 호환 |

**출처**:
- Crane Co. (2009). *Technical Paper No. 410: Flow of Fluids Through Valves, Fittings, and Pipe*. 26th printing.
- ASME B16.9 (2018). *Factory-Made Wrought Buttwelding Fittings*. (레듀서 치수/각도)

### 3.7 Hardy-Cross 반복법 (Grid 모드)

폐루프 배관망에서 유량 분배를 수렴시키는 반복법입니다.

**기본 원리**: 각 폐루프에서 에너지 보존 (순환 수두 = 0)을 만족하도록 유량을 보정

$$\Delta Q = -\frac{\sum h_f}{\sum \frac{\partial h_f}{\partial Q}}$$

| 파라미터 | 기본값 | 범위 | 설명 |
|---------|--------|------|------|
| 최대 반복 횟수 | 1,000 | — | 대규모 안전 마진 |
| 수두 허용 오차 | 0.001 m | — | 루프 수두 균형 기준 (약 0.01 kPa) |
| 유량 허용 오차 | 0.0001 LPM | — | 보정값 수렴 기준 |
| Under-relaxation | 0.5 | 0.1~1.0 | 수렴 안정성 제어 (감쇠 계수) |

**출처**: Cross, H. (1936). "Analysis of Flow in Networks of Conduits or Conductors." *Bulletin No. 286, University of Illinois Engineering Experiment Station*.

### 3.8 3항 분리 손실 모델

FiPLSim은 시스템 총 손실을 3가지 성분으로 분리합니다:

$$\Delta P_{total} = \Delta P_{pipe} + \Delta P_{fitting} + \Delta P_{bead}$$

| 성분 | 의미 | 포함 항목 |
|------|------|---------|
| Delta_P_pipe | 배관 마찰 손실 | 직관 Darcy-Weisbach 주손실 |
| Delta_P_fitting | 이음쇠 기본 손실 | K1_base + K2 + K3 + K_TEE_RUN + 레듀서 + 밸브 |
| Delta_P_bead | 비드 추가 손실 | K1_welded - K1_base (비드 효과분만 분리) |

이 3항 분리를 통해 비드 효과(Delta_P_bead)를 다른 손실과 독립적으로 정량화할 수 있습니다.

---

## 4. 프로그램 Validation (검증)

### 4.1 이중 검증 체계

FiPLSim은 **두 가지 독립적인 코드 경로**로 동일한 결과를 계산하여 교차 검증합니다:

| 경로 | 함수 | 방법 |
|------|------|------|
| 수리계산 (Analytical) | `calculate_system_delta_p()` | DynamicSystem 객체 없이 순수 수식만으로 Delta_P 직접 계산 |
| 시뮬레이션 (Simulation) | `generate_dynamic_system()` + `calculate_dynamic_system()` | 배관망 객체 생성 후 순회 방식으로 압력 프로파일 계산 |

### 4.2 검증 결과 (v3.0 — 2026-03-13)

`outputs/system_characterization.json` 기준:

| 검증 항목 | Case B (신기술) | Case A (기존기술) | 판정 |
|----------|----------------|-----------------|------|
| 수리계산 Delta_P | 0.431401 MPa | 0.445114 MPa | — |
| 시뮬레이션 Delta_P (입구압 0.5314) | 0.431401 MPa | 0.445114 MPa | — |
| 시뮬레이션 Delta_P (입구압 1.0) | 0.431401 MPa | 0.445114 MPa | — |
| **교차 검증 오차** | **0.0%** | **0.0%** | **PASS** |
| **입구 압력 독립성** | **0.0%** | **0.0%** | **PASS** |

- **교차 검증**: 수리계산과 시뮬레이션의 Delta_P가 동일
- **입구 압력 독립성**: 입구 압력을 바꿔도 Delta_P가 동일 (물리 법칙 정합성)

### 4.3 NFPC 103 기준 검증

| 항목 | 기준값 | 설계값 | 판정 |
|------|--------|--------|------|
| 말단 최소 방수압 | >= 0.1 MPa | Case B: 0.100 MPa (정확히 경계) | PASS |
| 말단 최대 방수압 | <= 1.2 MPa | Case B: 0.100 MPa | PASS |
| 헤드 1개 최소 방수량 | >= 80 LPM | 80 LPM (= 2560 / 32) | PASS |
| 가지배관 유속 | <= 6.0 m/s | 관경별 자동 확인 | 확인필요 |

### 4.4 테스트 수트

| 테스트 파일 | 테스트 수 | 검증 대상 |
|------------|----------|---------|
| `test_grid.py` | 46 | Grid 배관망, Hardy-Cross 수렴, 루프 에너지 균형 |
| `test_integration.py` | 48 | 통합 테스트, 대규모 배관망, 데이터 구조 정합성 |
| `test_valve.py` | 63 | 밸브/기기류, 레듀서 K값, K2 토글, 3항 분리 |
| **합계** | **157** | |

---

## 5. 손실 설계 내용

### 5.1 P_ref 기준압력 산출

**NFPC 103 역산법**으로 기준 입구 압력을 결정합니다:

$$P_{ref} = P_{terminal,min} + \Delta P_{system,B}$$

$$P_{ref} = 0.1 + 0.4314 = \textbf{0.5314 MPa}$$

| 항목 | 값 | 근거 |
|------|-----|------|
| 말단 최소 방수압 | 0.1 MPa | NFPC 103 규정 |
| 시스템 손실 (Case B) | 0.4314 MPa | 수리계산 역산 결과 |
| **P_ref** | **0.5314 MPa** | NFPC 103 최소 말단 충족 기준 |

> **P_ref 산출 시 적용 파라미터** (run_paper_simulations.py 기준):
> - branch_inlet_config = **"80A-65A"** (constants.py 기본값 "80A-50A"와 다름)
> - head_spacing_m = **2.1 m** (constants.py 기본값 2.3 m과 다름)
> - branch_spacing_m = 3.5 m, num_branches = 4, heads_per_branch = 8
> - 나머지: 기본값 동일 (K1=0.5, K2=2.5, K3=1.0, reducer_mode="crane")

### 5.2 손실 4항 분리 (P_ref 기준)

**Case B (신기술, 비드 0mm):**

| 손실 항목 | 값 (MPa) | 비율 |
|----------|---------|------|
| 배관 마찰 (pipe) | 0.1142 | 26.5% |
| 이음쇠 기본 (fitting) | 0.3172 | 73.5% |
| 비드 추가 (bead) | 0.0000 | 0.0% |
| 장비류 (equipment) | 0.0835 | (fitting에 포함) |
| **총 손실** | **0.4314** | 100% |

**Case A (기존기술, 비드 1.5mm):**

| 손실 항목 | 값 (MPa) | 비율 |
|----------|---------|------|
| 배관 마찰 (pipe) | 0.1142 | 25.6% |
| 이음쇠 기본 (fitting) | 0.3172 | 71.3% |
| 비드 추가 (bead) | 0.0137 | 3.1% |
| 장비류 (equipment) | 0.0835 | (fitting에 포함) |
| **총 손실** | **0.4451** | 100% |

### 5.3 비드 효과 정량화

| 지표 | 값 |
|------|-----|
| Case A 말단 압력 @P_ref | 0.0863 MPa (**FAIL**, < 0.1 MPa) |
| Case B 말단 압력 @P_ref | 0.1000 MPa (**PASS**, = 0.1 MPa) |
| 비드 손실 (Delta_P_bead) | 0.0137 MPa (13.7 kPa) |
| 말단 압력 개선율 | +15.9% |

**의미**: 비드 1.5mm의 추가 손실(13.7 kPa)이 Case A의 말단 압력을 NFPC 103 기준(0.1 MPa) 미달로 만드는 원인입니다.

---

## 6. 입력 변수 정리

### 6.1 배관 구조 입력

| 변수명 | 기본값 | 범위 | 단위 | 설명 |
|--------|--------|------|------|------|
| num_branches | 4 | 1~200 | 개 | 가지배관 수 |
| heads_per_branch | 8 | 1~50 | 개 | 가지배관당 헤드 수 |
| branch_spacing_m | 3.5 | > 0 | m | 가지배관 간격 (교차배관 위) |
| head_spacing_m | 2.3 | > 0 | m | 헤드 간격 (가지배관 위) — P_ref 산출 시 2.1 m 사용 |
| branch_inlet_config | "80A-50A" | 3종 선택 | — | 가지배관 분기 구조 (P_ref 산출 시 "80A-65A" 사용) |

### 6.2 수리학 입력

| 변수명 | 기본값 | 범위 | 단위 | 설명 |
|--------|--------|------|------|------|
| inlet_pressure_mpa | P_ref | > 0 | MPa | 시스템 입구 압력 |
| total_flow_lpm | 자동계산 | > 0 | LPM | 설계 유량 (= 총헤드수 * 80) |
| bead_height_mm | 1.5 | 0.1~5.0 | mm | 기존 기술 비드 돌출 높이 |
| bead_height_std_mm | 0.0 | 0.0~2.0 | mm | 비드 높이 표준편차 (비균일 모델) |
| use_head_fitting | True | True/False | — | 헤드이음쇠 사용 여부 |
| reducer_mode | "crane" | 4종 선택 | — | 레듀서 손실 계산 모드 |

### 6.3 K-factor 입력

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| K1_BASE | 0.5 | 배관이음쇠 기본 K (비드 0mm) |
| K2 | 2.5 | 헤드이음쇠 K (있음) |
| K2_WITHOUT_HEAD_FITTING | 1.4 | 헤드이음쇠 K (없음) |
| K3 | 1.0 | 분기 입구 K (Tee-Branch) |
| K_TEE_RUN | 0.3 | 교차배관 직진 K (Tee-Run) |

### 6.4 몬테카를로 입력

| 변수명 | 기본값 | 범위 | 설명 |
|--------|--------|------|------|
| mc_iterations | 100 | 10~100,000 | MC 반복 횟수 |
| min_defects | 1 | 0~total | 최소 결함(비드) 수 |
| max_defects | 3 | min~total | 최대 결함(비드) 수 |
| p_bead (베르누이) | 0.5 | 0.01~0.99 | 각 접합부의 비드 발생 확률 |

### 6.5 물성치 상수 (고정)

| 상수 | 값 | 단위 | 조건 |
|------|-----|------|------|
| rho (밀도) | 998.0 | kg/m^3 | 물, 20도C |
| mu (동점성계수) | 1.002 * 10^-3 | Pa*s | 물, 20도C |
| nu (운동점성계수) | 1.004 * 10^-6 | m^2/s | mu / rho |
| epsilon (절대 조도) | 0.045 | mm | 탄소강 신관 |
| g (중력가속도) | 9.81 | m/s^2 | — |

---

## 7. 출력값 정리

### 7.1 주요 출력

| 출력 항목 | 단위 | 설명 |
|----------|------|------|
| terminal_A_mpa | MPa | Case A 최악 말단 압력 |
| terminal_B_mpa | MPa | Case B 최악 말단 압력 |
| improvement_pct | % | 말단 압력 개선율 ((B-A)/A * 100) |
| pass_fail_A | PASS/FAIL | Case A NFPC 103 판정 (>= 0.1 MPa) |
| pass_fail_B | PASS/FAIL | Case B NFPC 103 판정 |
| loss_pipe_mpa | MPa | 배관 마찰 손실 |
| loss_fitting_mpa | MPa | 이음쇠 기본 손실 |
| loss_bead_mpa | MPa | 비드 추가 손실 |
| worst_branch_index | 정수 | 최악 가지배관 번호 (0-indexed) |

### 7.2 가지배관 상세 프로파일

각 헤드 구간별로 출력되는 상세 데이터:

| 필드 | 단위 | 설명 |
|------|------|------|
| head_number | 정수 | 헤드 번호 (1부터) |
| pipe_size | 문자열 | 호칭 구경 (예: "50A") |
| flow_lpm | LPM | 해당 구간 유량 |
| velocity_ms | m/s | 유속 |
| reynolds | 무차원 | 레이놀즈 수 |
| friction_factor | 무차원 | Darcy 마찰계수 |
| major_loss_mpa | MPa | 직관 마찰 손실 |
| K1_loss_mpa | MPa | 배관이음쇠 손실 (비드 포함) |
| K2_loss_mpa | MPa | 헤드이음쇠 손실 |
| reducer_loss_mpa | MPa | 레듀서 손실 (관경 전환 시) |
| total_seg_loss_mpa | MPa | 해당 구간 총 손실 |
| pressure_after_mpa | MPa | 해당 구간 후 잔여 압력 |

### 7.3 몬테카를로 출력

| 출력 | 설명 |
|------|------|
| terminal_pressures | 각 반복의 말단 압력 배열 |
| mean_terminal_mpa | 말단 압력 평균 |
| std_terminal_mpa | 말단 압력 표준편차 |
| fail_rate | NFPC 103 기준 실패율 (말단 < 0.1 MPa) |
| bead_distribution | 비드 위치 분포 통계 |

### 7.4 출력 파일 구조

```
outputs/
  system_characterization.json    (P_ref, 검증 결과, 4항 분리)
  stage1_기반확립/                  (기본 비교 CSV, PNG, DOCX)
  stage2_파라메트릭/                (파라미터 스윕 CSV, PNG, DOCX)
  stage3_확률론적/                  (MC 결과 CSV, PNG, DOCX)
```

---

## 8. 설계 변경 비교표 (이전 vs 현재)

### 8.1 주요 변경 사항 요약

| 항목 | 이전 설계 (v2.x) | 현재 설계 (v3.0) | 변경 근거 |
|------|-----------------|-----------------|---------|
| **WeldBead 클래스** | 존재 (직관 용접 비드 모델) | **완전 제거** | 실제 현장은 레듀서/메커니컬 조인트 사용, 직관 용접 비드 부재 |
| **beads_per_branch** | 입력 변수 (기본 5개) | **삭제** | WeldBead 제거에 따른 불필요 파라미터 |
| **레듀서 손실** | 미반영 | **Crane TP-410 기반 4모드** | 관경 전환점 레듀서 K값 누락 보완 |
| **K2 헤드이음쇠** | 항상 K2=2.5 고정 | **토글 (2.5 / 1.4)** | 헤드이음쇠 유무에 따른 선택 가능 |
| **P_ref 산출** | P_cal = P_min * 1.3 (안전계수) | **NFPC 103 역산** (0.1 + Delta_P_B) | 법적 근거 없는 안전계수 1.3 폐기 |
| **Grid 3항 분리** | 미반환 (KeyError) | **완전 반환** | Tree/Grid 모드 간 출력 일관성 확보 |

### 8.2 삭제된 요소 상세

| 삭제 항목 | 이전 위치 | 삭제 사유 |
|----------|----------|---------|
| `WeldBead` 클래스 | pipe_network.py | 실제 배관은 직관 용접이 아닌 레듀서/조인트 사용 |
| `generate_branch_beads()` 함수 | pipe_network.py | WeldBead 생성 함수 — 불필요 |
| `beads_per_branch` 파라미터 | 전체 6개 파일 | 직관 비드 개수 입력 — 불필요 |
| `bead_height_for_weld_mm` 파라미터 | simulation.py, pump.py | 직관 비드 전용 높이 — 불필요 |
| `DEFAULT_BEADS_PER_BRANCH` 상수 | constants.py | 삭제된 파라미터의 기본값 |
| `test_weld_beads.py` 테스트 파일 | 프로젝트 루트 | WeldBead 전용 테스트 38개 — 대상 삭제 |

### 8.3 추가된 요소 상세

| 추가 항목 | 위치 | 추가 사유 |
|----------|------|---------|
| `k_reducer()` 함수 | hydraulics.py | Crane TP-410 레듀서 K값 계산 |
| `_calc_reducer_loss_mpa()` 함수 | pipe_network.py | 관경 전환 시 레듀서 압력 손실 |
| `REDUCER_ANGLES_DEG` 딕셔너리 | constants.py | ASME B16.9 레듀서 원뿔 각도 |
| `REDUCER_MODE_*` 상수 4종 | constants.py | 레듀서 손실 모드 선택 |
| `K2_WITHOUT_HEAD_FITTING` 상수 | constants.py | 헤드이음쇠 없음 시 K2=1.4 |
| `DEFAULT_USE_HEAD_FITTING` 상수 | constants.py | 헤드이음쇠 토글 기본값 |
| `use_head_fitting` 파라미터 | 전체 파일 체인 | K2 토글 전파 |
| `reducer_mode` 파라미터 | 전체 파일 체인 | 레듀서 모드 전파 |
| `reducer_k_fixed` 파라미터 | 전체 파일 체인 | 고정 K값 모드 전파 |

### 8.4 P_ref 산출 방식 변경

| 항목 | 이전 (v2.x) | 현재 (v3.0) |
|------|-------------|-------------|
| **방식** | P_cal = P_min * 1.3 | P_ref = 0.1 + Delta_P_system_B |
| **근거** | 안전계수 1.3 (법적 근거 없음) | NFPC 103 최소 말단 0.1 MPa + 시스템 손실 역산 |
| **P_min** | 수리계산 최저 입구압 | — (개념 폐기) |
| **결과값** | P_cal = 0.5318 * 1.3 = 0.69 MPa (과대) | P_ref = 0.5314 MPa |
| **검증** | 단일 경로 | 이중 경로 (수리계산 + 시뮬레이션 교차 검증) |

### 8.5 수치 변화 비교

| 지표 | 이전 (v2.x) | 현재 (v3.0) | 변화 |
|------|-------------|-------------|------|
| P_ref | 0.5318 MPa | 0.5314 MPa | -0.4 kPa |
| Delta_P_B (신기술) | — | 0.4314 MPa | 기준값 |
| Delta_P_A (기존기술) | — | 0.4451 MPa | +13.7 kPa 비드 효과 |
| 비드 손실 (1.5mm) | 비드+WeldBead 혼합 | 0.0137 MPa (이음쇠 비드만) | 순수 비드 효과 분리 |
| 레듀서 손실 | 0 MPa | 미소 (K 합계 0.081) | 신규 반영 |
| 테스트 수 | 195개 | 157개 | -38개 (WeldBead 테스트 삭제) |

### 8.6 변경이 결과에 미치는 영향

| 변경 | 영향 방향 | 크기 |
|------|----------|------|
| WeldBead(직관비드) 제거 | 손실 감소, 말단 압력 상승 | 중간 |
| 레듀서 손실 추가 | 손실 증가, 말단 압력 하락 | 미소 (K합 0.081) |
| K2 토글 (2.5→1.4 선택 시) | 손실 크게 감소 | 큼 |
| **순효과 (기본 설정)** | **P_ref 0.4 kPa 감소** | **미미** |

순효과가 미미한 이유: WeldBead 제거 효과와 레듀서 추가 효과가 거의 상쇄되며, 기본 K2=2.5(헤드이음쇠 있음)는 변경 없으므로 전체 결과 변화가 매우 작습니다.

---

## 9. 참고 문헌

| # | 출처 | 적용 위치 |
|---|------|---------|
| 1 | Crane Co. (2009). *Technical Paper No. 410: Flow of Fluids Through Valves, Fittings, and Pipe*. 26th printing. | K-factor 전체, K2, K_TEE_RUN, 레듀서 K |
| 2 | ASME B16.9 (2018). *Factory-Made Wrought Buttwelding Fittings*. | 레듀서 원뿔 각도 (theta) |
| 3 | Moody, L.F. (1944). "Friction Factors for Pipe Flow." *Trans. ASME*, 66(8), 671-684. | Darcy-Weisbach 주손실 |
| 4 | Colebrook, C.F. (1939). "Turbulent Flow in Pipes." *J. ICE*, 11(4), 133-156. | Colebrook-White 마찰계수 |
| 5 | Idelchik, I.E. (1966). *Handbook of Hydraulic Resistance*. Hemisphere Publishing. | Borda-Carnot 기반 K_eff 모델 |
| 6 | Cross, H. (1936). "Analysis of Flow in Networks." *Bulletin No. 286, UIUC*. | Hardy-Cross 반복법 |
| 7 | NFPC 103 (소방시설의 화재안전기준). 국민안전처. | 말단 방수압 0.1~1.2 MPa, 방수량 80 LPM |
| 8 | NFTC 103 (소방시설의 기술기준). 국민안전처. | 배관 구경 선정, 유속 제한 |
| 9 | JIS G 3452 / KS D 3507. *Carbon Steel Pipes for Ordinary Piping*. | Schedule 40 배관 치수 |

---

## 부록 A: 대표 유속/레이놀즈수 참조표

**조건: 설계 유량 2,560 LPM, 4분기, 가지배관 유량 640 LPM**

| 관경 | 내경 (mm) | 유속 (m/s) | Re | 마찰계수 f |
|------|-----------|-----------|-------|----------|
| 80A | 77.92 | 2.24 | 173,601 | 0.01945 |
| 65A | 62.71 | 3.45 | 215,707 | 0.01975 |
| 50A | 52.51 | 4.93 | 257,608 | 0.02015 |
| 40A | 40.90 | 8.12 | 330,734 | 0.02093 |
| 32A | 35.04 | 11.06 | 386,045 | 0.02153 |
| 25A | 26.64 | 19.14 | 507,770 | 0.02281 |

> 참고: 위 유속은 가지배관 입구 유량(640 LPM) 기준이며, 실제로는 각 헤드에서 80 LPM씩 분기되어 하류로 갈수록 유속이 감소합니다.
