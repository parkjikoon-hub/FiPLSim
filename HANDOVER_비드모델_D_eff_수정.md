# 인수인계 자료: 비드 모델 D_eff 수정 작업

## 1. 현재 상황 요약

**논문**: "Stochastic assessment of weld bead effects on fire sprinkler piping networks: A dual-scenario Monte Carlo simulation study" (Fire Safety Journal 제출)

**문제**: FiPLSim의 비드 효과가 논문 목표 대비 **3.3배 작음**
- 논문 목표: 비드 2.5mm, Q=2100 LPM → 비드 손실 ≈ 60.7 kPa
- FiPLSim 결과: 비드 손실 ≈ 18.2 kPa (3.3배 부족)

**원인**: 비드의 유효내경(D_eff)을 **K-factor(국부 손실)에만 적용**하고, **Darcy-Weisbach 마찰손실(주손실)과 유속 계산에는 미적용**

---

## 2. 핵심 문제 상세

### 현재 코드 (pipe_network.py, 406~426행)

```python
# _calculate_branch_profile() 내부
for i, junc in enumerate(branch.junctions):
    seg = junc.pipe_segment
    segment_flow = total_flow - (i * head_flow)

    # ❌ 문제: 원래 내경(D)으로 유속 계산 → 비드 효과 미반영
    V = velocity_from_flow(segment_flow, seg.inner_diameter_m)
    Re = reynolds_number(V, seg.inner_diameter_m)
    f = friction_factor(Re, D=seg.inner_diameter_m)

    # ❌ 문제: 원래 내경(D)으로 마찰손실 계산
    p_major = head_to_mpa(major_loss(f, seg.length_m, seg.inner_diameter_m, V))

    # ✅ K-factor만 D_eff 적용됨 (K1_welded = K_base × (D/D_eff)⁴)
    p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V))
    p_K2 = head_to_mpa(minor_loss(junc.K2_head, V))
```

### 논문이 기대하는 모델

비드가 있는 접합부(junction)에서는:

1. **유효내경**: D_eff = D - 2h (h = 비드 높이)
2. **유속**: V_eff = Q / A_eff (D_eff 기반 단면적)
3. **마찰손실**: f × (L/D_eff) × V_eff²/(2g) ← **D_eff 사용**
4. **K-factor 손실**: K_eff × V_eff²/(2g) ← **이미 구현됨**

비드가 없는 접합부에서는 기존 D 그대로 사용.

### 물리적 의미

비드가 배관 내벽에 돌출되면:
- 단면적 감소 → **유속 증가** (V ∝ 1/D²)
- 유효 내경 감소 → **마찰계수 변화** + **L/D 비율 증가**
- 결과: K-factor 손실만 적용할 때보다 **훨씬 큰 압력 강하**

---

## 3. 수정 방법

### 수정 파일: `pipe_network.py`의 `_calculate_branch_profile()` (375행~)

#### 수정 전 (현재)
```python
for i, junc in enumerate(branch.junctions):
    seg = junc.pipe_segment
    segment_flow = total_flow - (i * head_flow)
    V = velocity_from_flow(segment_flow, seg.inner_diameter_m)
    Re = reynolds_number(V, seg.inner_diameter_m)
    f = friction_factor(Re, D=seg.inner_diameter_m)
    p_major = head_to_mpa(major_loss(f, seg.length_m, seg.inner_diameter_m, V))
```

#### 수정 후 (제안)
```python
for i, junc in enumerate(branch.junctions):
    seg = junc.pipe_segment
    segment_flow = total_flow - (i * head_flow)

    # 비드가 있으면 D_eff 사용, 없으면 원래 D 사용
    D_actual = seg.inner_diameter_m
    if junc.bead_height_mm > 0:
        D_eff = D_actual - 2.0 * junc.bead_height_mm / 1000.0  # mm → m
        if D_eff > 0:
            D_actual = D_eff

    V = velocity_from_flow(segment_flow, D_actual)
    Re = reynolds_number(V, D_actual)
    f = friction_factor(Re, D=D_actual)
    p_major = head_to_mpa(major_loss(f, seg.length_m, D_actual, V))
    p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V))
    p_K2 = head_to_mpa(minor_loss(junc.K2_head, V))
```

**핵심 변경**: `seg.inner_diameter_m` 대신 비드 여부에 따라 `D_eff` 또는 `D` 선택

### 주의사항

1. **WeldBead(직관 구간 용접 비드)도 동일 처리 필요**: 418~421행의 `beads_in_seg` 부분도 D_eff 기반 유속으로 계산해야 할 수 있음
2. **hardy_cross.py에도 동일 수정 적용**: Grid 토폴로지의 압력 계산에도 비드 D_eff 적용 필요
3. **기존 테스트 130개**: 변경 후 반드시 `python3 -m pytest test_integration.py test_weld_beads.py test_grid.py` 실행하여 통과 확인
4. **seg_details 딕셔너리의 velocity_ms, inner_diameter_mm 등도 D_eff 값으로 업데이트** 필요

---

## 4. 수정 후 검증 순서

### Step 1: 코드 수정
`pipe_network.py`의 `_calculate_branch_profile()` 수정

### Step 2: 단위 테스트 실행
```bash
PYTHONIOENCODING=utf-8 python3 -m pytest test_integration.py test_weld_beads.py test_grid.py -v
```

### Step 3: 기준선 검증
- Case B (bead=0): **기존과 동일해야 함** (0.156287 MPa) — 비드 없으면 D_eff = D이므로 결과 불변
- Case A (bead=2.5mm 전체): **기존보다 크게 낮아져야 함** — 논문 목표 0.0956 MPa

### Step 4: Dual Scenario 재실행
```bash
python3 export_dual_scenario.py
```
- Scenario 1 (p=0.5, bead 2.5mm): 논문 목표 μ≈0.1100 MPa, Pf≈2.43%
- Scenario 2 (p_b=0.5): 논문 목표 μ≈0.1035 MPa, Pf≈12.63%

---

## 5. 논문 검증 목표값 (최종 달성해야 할 수치)

### Scenario 1 (결함 집중 모델) — Table 7
| 비드 | Q(LPM) | μ(MPa) | σ(MPa) | Pf(%) |
|------|--------|--------|--------|-------|
| 2.5mm | 2100 | 0.1100 | 0.0048 | 2.43 |
| 2.0mm | 2100 | 0.1199 | 0.0039 | 0.37 |

### Scenario 2 (시공 품질 모델) — Table 13b
| p_b | μ(MPa) | σ(MPa) | Pf(%) |
|-----|--------|--------|-------|
| 0.1 | 0.1487 | 0.0018 | 0.00 |
| 0.3 | 0.1337 | 0.0030 | 0.00 |
| 0.5 | 0.1035 | 0.0030 | 12.63 |
| 0.7 | 0.0900 | 0.0028 | 85.89 |
| 0.9 | 0.0765 | 0.0012 | 100.00 |

### 기준선 결정론적 값 (Table 8, Q=2100 LPM)
- Case B (bead=0): 0.1563 MPa ← **현재 FiPLSim 완벽 일치** ✅
- Case A (bead=2.5mm 전체): 0.0956 MPa ← 현재 FiPLSim 0.1381 MPa (42.5 kPa 차이) ❌

---

## 6. 관련 파일 목록

| 파일 | 역할 | 수정 필요 |
|------|------|----------|
| `pipe_network.py` | 압력 계산 핵심 — `_calculate_branch_profile()` (375행) | **⬤ 핵심 수정 대상** |
| `hardy_cross.py` | Grid 토폴로지 압력 계산 | △ Grid 사용 시 동일 수정 필요 |
| `hydraulics.py` | Darcy-Weisbach, K-factor 함수 (변경 불필요) | ✕ |
| `constants.py` | K값, 배관 치수 (변경 불필요) | ✕ |
| `simulation.py` | MC 시뮬레이션 (pipe_network.py 호출) | ✕ |
| `export_dual_scenario.py` | 논문 Dual Scenario 데이터 출력 스크립트 | ✕ (수정 후 재실행만) |
| `export_scenario1_single_branch.py` | ❌ 잘못된 첫 시도 (num_branches=1) — 사용하지 말 것 | 삭제 가능 |
| `export_paper_valve_off.py` | 밸브 OFF 검증 스크립트 | ✕ |

---

## 7. 현재까지 생성된 데이터 파일

1. `FiPLSim_논문검증_밸브OFF_데이터.xlsx` (44 KB) — Case B 검증 완료
2. `FiPLSim_DualScenario_논문재현_데이터.xlsx` (738 KB) — **D_eff 수정 전** 결과 (비드 효과 과소)
3. `FiPLSim_Scenario1_SingleBranch_데이터.xlsx` — ❌ 잘못된 결과 (삭제 가능)

---

## 8. 요약: 다음 대화에서 해야 할 일

1. **`pipe_network.py`의 `_calculate_branch_profile()` 수정**: 비드가 있는 junction에서 D_eff를 유속·마찰손실·국부손실 모두에 적용
2. **테스트 실행**: 기존 130개 테스트 통과 확인
3. **`export_dual_scenario.py` 재실행**: 논문 목표값 재현 확인
4. (선택) `hardy_cross.py`에도 동일 D_eff 수정 적용

**핵심 한 줄 요약**: "비드가 있으면 `seg.inner_diameter_m` 대신 `D_eff = D - 2h`를 유속·마찰·국부손실 전체에 적용하라"
