"""
논문 Dual-Scenario MC 시뮬레이션 — Scenario 1 + Scenario 2 통합 데이터 출력

논문 조건 (Section 2.3, 2.5):
  - 시스템: 4 branches × 8 heads = 32 junctions, tree topology
  - 입구 압력: 0.4 MPa
  - 밸브: OFF
  - 비드 높이: 2.5 mm (+ 2.0 mm 비교)
  - N = 10,000

Scenario 1 (결함 집중 모델, Section 2.5.1):
  - worst branch(최악 가지배관) 1개의 8개 junction에만 비드 배치
  - 나머지 3개 branch는 bead=0 고정
  - p = 0.5 (최대 엔트로피)
  - 기대 비드 수 ≈ 4개

Scenario 2 (시공 품질 모델, Section 2.5.2):
  - 전체 32개 junction에 독립 Bernoulli 시행
  - p_b = 0.1, 0.3, 0.5, 0.7, 0.9
  - 기대 비드 수 = 32 × p_b

논문 목표: Scenario 1, p=0.5, bead 2.5mm, Q=2100 → μ≈0.1100 MPa, Pf≈2.43%

출력: FiPLSim_DualScenario_논문재현_데이터.xlsx
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from constants import (
    PIPE_DIMENSIONS, get_inner_diameter_m, RHO, G,
    MIN_TERMINAL_PRESSURE_MPA, K1_BASE, K2, K3,
)
from hydraulics import velocity_from_flow
from pipe_network import generate_dynamic_system, calculate_dynamic_system
from simulation import run_bernoulli_monte_carlo

# ══════════════════════════════════════════════
# 공통 설정
# ══════════════════════════════════════════════
NUM_BR = 4
HEADS = 8
BRANCH_SP = 3.5
HEAD_SP = 2.3
INLET_P = 0.4        # 0.4 MPa
SUPPLY = "80A"
N_ITER = 10000

D_supply = get_inner_diameter_m(SUPPLY)
D_supply_mm = PIPE_DIMENSIONS[SUPPLY]["id_mm"]

print("=" * 70)
print("  FiPLSim — Dual-Scenario MC (논문 재현)")
print("=" * 70)
print(f"  {NUM_BR} branches × {HEADS} heads = {NUM_BR * HEADS} junctions")
print(f"  입구 압력: {INLET_P} MPa | 밸브: OFF | MC: {N_ITER:,}회")
t0 = time.time()

# ══════════════════════════════════════════════
# Sheet 1: 시뮬레이션 조건
# ══════════════════════════════════════════════
df_cond = pd.DataFrame({
    "항목": [
        "가지배관 수", "가지배관당 헤드", "전체 junction 수",
        "가지배관 간격 (m)", "헤드 간격 (m)", "입구 압력 (MPa)",
        "라이저 관경", "밸브 상태", "MC 반복 횟수",
        "말단 최소 기준 (MPa)",
        "Scenario 1 범위", "Scenario 2 범위",
    ],
    "값": [
        NUM_BR, HEADS, NUM_BR * HEADS,
        BRANCH_SP, HEAD_SP, INLET_P,
        SUPPLY, "OFF", N_ITER,
        MIN_TERMINAL_PRESSURE_MPA,
        "worst branch 8 junctions만",
        "전체 32 junctions",
    ],
})


# ══════════════════════════════════════════════
# Scenario 1 전용 MC 함수
# (worst branch = 마지막 branch에만 비드, 나머지 bead=0)
# ══════════════════════════════════════════════
def run_scenario1_mc(
    p_bead, n_iterations, bead_height_mm,
    num_branches, heads_per_branch,
    branch_spacing_m, head_spacing_m,
    inlet_pressure_mpa, total_flow_lpm,
):
    """
    Scenario 1: worst branch(마지막 가지배관)의 8개 junction에만
    독립 Bernoulli(p_bead) 비드 배치. 나머지 branch는 bead=0.
    """
    rng = np.random.default_rng()
    worst_pressures = np.zeros(n_iterations)
    bead_counts = np.zeros(n_iterations, dtype=int)

    # worst branch = 마지막 branch (교차배관 손실 최대)
    worst_branch_idx = num_branches - 1

    for trial in range(n_iterations):
        # 비드 배열 초기화: 전체 bead=0
        beads_2d = [[0.0] * heads_per_branch for _ in range(num_branches)]

        # worst branch에만 Bernoulli 비드 배치
        rand_vals = rng.uniform(0, 1, size=heads_per_branch)
        count = 0
        for h in range(heads_per_branch):
            if rand_vals[h] <= p_bead:
                beads_2d[worst_branch_idx][h] = bead_height_mm
                count += 1
        bead_counts[trial] = count

        # 시스템 계산
        system = generate_dynamic_system(
            num_branches=num_branches,
            heads_per_branch=heads_per_branch,
            branch_spacing_m=branch_spacing_m,
            head_spacing_m=head_spacing_m,
            inlet_pressure_mpa=inlet_pressure_mpa,
            total_flow_lpm=total_flow_lpm,
            bead_heights_2d=beads_2d,
        )
        result = calculate_dynamic_system(
            system, K3,
            equipment_k_factors=None,  # 밸브 OFF
            supply_pipe_size=SUPPLY,
        )
        worst_pressures[trial] = result["worst_terminal_mpa"]

    below = np.sum(worst_pressures < MIN_TERMINAL_PRESSURE_MPA)

    return {
        "terminal_pressures": worst_pressures,
        "bead_counts": bead_counts,
        "mean_pressure": float(np.mean(worst_pressures)),
        "std_pressure": float(np.std(worst_pressures, ddof=1)) if n_iterations > 1 else 0.0,
        "min_pressure": float(np.min(worst_pressures)),
        "max_pressure": float(np.max(worst_pressures)),
        "p_below_threshold": float(below / n_iterations),
        "mean_bead_count": float(np.mean(bead_counts)),
        "expected_bead_count": float(heads_per_branch * p_bead),
    }


# ══════════════════════════════════════════════
# 기준선: Case B (bead=0) & Case A (bead=2.5mm 전체)
# ══════════════════════════════════════════════
print("\n[1/5] 기준선 (deterministic)...")

# Case B: bead=0
sys_b = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
)
res_b = calculate_dynamic_system(sys_b, equipment_k_factors=None)
p_caseB = res_b["worst_terminal_mpa"]

# Case A: bead=2.5mm 전체 (p=1.0)
beads_all = [[2.5] * HEADS for _ in range(NUM_BR)]
sys_a = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
    bead_heights_2d=beads_all,
)
res_a = calculate_dynamic_system(sys_a, equipment_k_factors=None)
p_caseA_full = res_a["worst_terminal_mpa"]

print(f"  Case B (bead=0):       {p_caseB:.6f} MPa (논문: 0.1563)")
print(f"  Case A (bead=2.5 all): {p_caseA_full:.6f} MPa (논문: 0.0956)")


# ══════════════════════════════════════════════
# Sheet 2: 전유량 결정론적 스캔 (Table 8 재현)
# ══════════════════════════════════════════════
print("\n[2/5] 전유량 결정론적 스캔 (Table 8)...")
flows = [1000, 1200, 1400, 1600, 1800, 2000, 2100, 2200, 2300, 2400]
scan_rows = []

for Q in flows:
    # Case A: worst branch에만 bead=2.5 (Scenario 1 deterministic = p=1.0 for worst branch)
    beads_s1 = [[0.0] * HEADS for _ in range(NUM_BR)]
    beads_s1[NUM_BR - 1] = [2.5] * HEADS  # worst branch만

    sys_cA = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
        bead_heights_2d=beads_s1,
    )
    res_cA = calculate_dynamic_system(sys_cA, equipment_k_factors=None)
    pA = res_cA["worst_terminal_mpa"]

    # Case B: bead=0
    sys_cB = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
    )
    res_cB = calculate_dynamic_system(sys_cB, equipment_k_factors=None)
    pB = res_cB["worst_terminal_mpa"]

    rel_diff = ((pB - pA) / pA * 100) if pA > 0 else float('inf')
    pfA = "PASS" if pA >= MIN_TERMINAL_PRESSURE_MPA else "FAIL"
    pfB = "PASS" if pB >= MIN_TERMINAL_PRESSURE_MPA else "FAIL"

    scan_rows.append({
        "Q (LPM)": Q,
        "Case A (MPa)": round(pA, 4),
        "Case B (MPa)": round(pB, 4),
        "Rel. diff. (%)": round(rel_diff, 1) if pA > 0 else "—",
        "Case A": pfA,
        "Case B": pfB,
    })

    if Q in [1200, 1600, 2100]:
        print(f"  Q={Q:>5}: A={pA:.4f}, B={pB:.4f}, diff={rel_diff:.1f}%")

df_scan = pd.DataFrame(scan_rows)


# ══════════════════════════════════════════════
# Sheet 3: Scenario 1 MC (결함 집중 모델)
#   bead 2.5mm, p=0.5, Q=1200/1600/2100 (Table 7/9 재현)
#   + bead 2.0mm 비교
# ══════════════════════════════════════════════
print("\n[3/5] Scenario 1 MC (결함 집중 모델)...")
s1_flows = [1200, 1600, 2100]
bead_heights = [0.0, 2.0, 2.5]
s1_rows = []
s1_raw = {}  # 원시 데이터 저장

for bh in bead_heights:
    for Q in s1_flows:
        label = f"S1_bead{bh}_Q{Q}"

        if bh == 0.0:
            # Case B: deterministic (bead=0이면 MC 해도 σ=0)
            sys_det = generate_dynamic_system(
                num_branches=NUM_BR, heads_per_branch=HEADS,
                branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
                inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
            )
            res_det = calculate_dynamic_system(sys_det, equipment_k_factors=None)
            p_det = res_det["worst_terminal_mpa"]

            s1_rows.append({
                "Bead (mm)": bh, "Q (LPM)": Q,
                "μ (MPa)": round(p_det, 4), "σ (MPa)": 0.0,
                "Min (MPa)": round(p_det, 4), "Max (MPa)": round(p_det, 4),
                "Pf (%)": 0.0 if p_det >= MIN_TERMINAL_PRESSURE_MPA else 100.0,
            })
            print(f"  bead={bh}, Q={Q}: μ={p_det:.4f} (deterministic)")
        else:
            print(f"  bead={bh}, Q={Q}: ", end="", flush=True)
            t_s = time.time()

            res_mc = run_scenario1_mc(
                p_bead=0.5, n_iterations=N_ITER,
                bead_height_mm=bh,
                num_branches=NUM_BR, heads_per_branch=HEADS,
                branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
                inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
            )

            s1_rows.append({
                "Bead (mm)": bh, "Q (LPM)": Q,
                "μ (MPa)": round(res_mc["mean_pressure"], 4),
                "σ (MPa)": round(res_mc["std_pressure"], 4),
                "Min (MPa)": round(res_mc["min_pressure"], 4),
                "Max (MPa)": round(res_mc["max_pressure"], 4),
                "Pf (%)": round(res_mc["p_below_threshold"] * 100, 2),
            })

            # 원시 데이터 저장 (bead 2.5, Q=2100만)
            if bh == 2.5:
                s1_raw[f"Q{Q}_pressure"] = res_mc["terminal_pressures"]
                s1_raw[f"Q{Q}_beads"] = res_mc["bead_counts"]

            dt = time.time() - t_s
            print(f"μ={res_mc['mean_pressure']:.4f}, σ={res_mc['std_pressure']:.4f}, "
                  f"Pf={res_mc['p_below_threshold']*100:.2f}% ({dt:.1f}s)")

df_s1 = pd.DataFrame(s1_rows)


# ══════════════════════════════════════════════
# Sheet 4: Scenario 2 MC (시공 품질 모델)
#   bead 2.5mm, Q=2100, p_b=0.1/0.3/0.5/0.7/0.9 (Table 13b 재현)
# ══════════════════════════════════════════════
print("\n[4/5] Scenario 2 MC (시공 품질 모델)...")
p_values_s2 = [0.1, 0.3, 0.5, 0.7, 0.9]
s2_rows = []
s2_raw = {}

for pb in p_values_s2:
    print(f"  p_b={pb}: ", end="", flush=True)
    t_s = time.time()

    res_mc = run_bernoulli_monte_carlo(
        p_bead=pb, n_iterations=N_ITER,
        bead_height_mm=2.5,
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
        beads_per_branch=0, topology="tree",
        equipment_k_factors=None,   # 밸브 OFF
        supply_pipe_size=SUPPLY,
    )

    margin = (res_mc["mean_pressure"] - 0.1) / 0.1 * 100

    s2_rows.append({
        "p_b": pb,
        "μ (MPa)": round(res_mc["mean_pressure"], 4),
        "σ (MPa)": round(res_mc["std_pressure"], 4),
        "Min (MPa)": round(res_mc["min_pressure"], 4),
        "Max (MPa)": round(res_mc["max_pressure"], 4),
        "E[beads]": round(res_mc["expected_bead_count"], 1),
        "Margin (%)": round(margin, 1),
        "Pf (%)": round(res_mc["p_below_threshold"] * 100, 2),
    })

    s2_raw[f"pb{pb}_pressure"] = res_mc["terminal_pressures"]
    s2_raw[f"pb{pb}_beads"] = res_mc["bead_counts"]

    dt = time.time() - t_s
    print(f"μ={res_mc['mean_pressure']:.4f}, σ={res_mc['std_pressure']:.4f}, "
          f"Pf={res_mc['p_below_threshold']*100:.2f}%, E[beads]={res_mc['expected_bead_count']:.1f} ({dt:.1f}s)")

df_s2 = pd.DataFrame(s2_rows)


# ══════════════════════════════════════════════
# Sheet 5: Scenario 1 p값 변화 (p=0.1~0.9, bead 2.5mm, Q=2100)
# ══════════════════════════════════════════════
print("\n[5/5] Scenario 1 p값 스캔 (bead 2.5mm, Q=2100)...")
p_values_s1 = [0.1, 0.3, 0.5, 0.7, 0.9]
s1p_rows = []

for p in p_values_s1:
    print(f"  p={p}: ", end="", flush=True)
    t_s = time.time()

    res_mc = run_scenario1_mc(
        p_bead=p, n_iterations=N_ITER,
        bead_height_mm=2.5,
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
    )

    pct = np.percentile(res_mc["terminal_pressures"], [5, 25, 50, 75, 95])

    s1p_rows.append({
        "p": p,
        "μ (MPa)": round(res_mc["mean_pressure"], 4),
        "σ (MPa)": round(res_mc["std_pressure"], 4),
        "Min (MPa)": round(res_mc["min_pressure"], 4),
        "Max (MPa)": round(res_mc["max_pressure"], 4),
        "P5% (MPa)": round(pct[0], 4),
        "P25% (MPa)": round(pct[1], 4),
        "P50% (MPa)": round(pct[2], 4),
        "P75% (MPa)": round(pct[3], 4),
        "P95% (MPa)": round(pct[4], 4),
        "E[beads]": round(res_mc["expected_bead_count"], 1),
        "Pf (%)": round(res_mc["p_below_threshold"] * 100, 2),
    })

    dt = time.time() - t_s
    print(f"μ={res_mc['mean_pressure']:.4f}, Pf={res_mc['p_below_threshold']*100:.2f}% ({dt:.1f}s)")

df_s1p = pd.DataFrame(s1p_rows)


# ══════════════════════════════════════════════
# Sheet 6: Dual-Scenario 비교 (Table 14 재현)
# ══════════════════════════════════════════════
s1_critical = next(r for r in s1_rows if r["Bead (mm)"] == 2.5 and r["Q (LPM)"] == 2100)
s2_critical = next(r for r in s2_rows if r["p_b"] == 0.5)

df_compare = pd.DataFrame({
    "항목": [
        "비드 적용 범위", "기대 비드 수",
        "μ (MPa)", "σ (MPa)", "Min (MPa)", "Max (MPa)", "Pf (%)",
    ],
    "Scenario 1 (결함 집중)": [
        "8 junctions (worst path)", "≈4",
        s1_critical["μ (MPa)"], s1_critical["σ (MPa)"],
        s1_critical["Min (MPa)"], s1_critical["Max (MPa)"], s1_critical["Pf (%)"],
    ],
    "Scenario 2 (시공 품질, p_b=0.5)": [
        "32 junctions (전체)", "16",
        s2_critical["μ (MPa)"], s2_critical["σ (MPa)"],
        s2_critical["Min (MPa)"], s2_critical["Max (MPa)"], s2_critical["Pf (%)"],
    ],
    "논문 Table 14": [
        "—", "—",
        0.1100, 0.0048, 0.0914, 0.1256, 2.43,
    ],
})


# ══════════════════════════════════════════════
# Sheet 7: MC 원시 분포 (Scenario 1, bead 2.5mm)
# ══════════════════════════════════════════════
dist_s1 = {}
for Q in s1_flows:
    key_p = f"Q{Q}_pressure"
    key_b = f"Q{Q}_beads"
    if key_p in s1_raw:
        dist_s1[f"S1_Q{Q}_pressure(MPa)"] = np.round(s1_raw[key_p], 6)
        dist_s1[f"S1_Q{Q}_beads"] = s1_raw[key_b]
df_dist_s1 = pd.DataFrame(dist_s1) if dist_s1 else pd.DataFrame()

# Sheet 8: MC 원시 분포 (Scenario 2, bead 2.5mm, Q=2100)
dist_s2 = {}
for pb in p_values_s2:
    key_p = f"pb{pb}_pressure"
    key_b = f"pb{pb}_beads"
    dist_s2[f"S2_pb{pb}_pressure(MPa)"] = np.round(s2_raw[key_p], 6)
    dist_s2[f"S2_pb{pb}_beads"] = s2_raw[key_b]
df_dist_s2 = pd.DataFrame(dist_s2)


# ══════════════════════════════════════════════
# 엑셀 저장
# ══════════════════════════════════════════════
output_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "FiPLSim_DualScenario_논문재현_데이터.xlsx",
)

with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    df_cond.to_excel(writer, sheet_name="1_조건", index=False)
    df_scan.to_excel(writer, sheet_name="2_전유량스캔_Table8", index=False)
    df_s1.to_excel(writer, sheet_name="3_S1_MC_Table7_9", index=False)
    df_s1p.to_excel(writer, sheet_name="4_S1_p스캔_bead2.5", index=False)
    df_s2.to_excel(writer, sheet_name="5_S2_MC_Table13b", index=False)
    df_compare.to_excel(writer, sheet_name="6_DualScenario비교", index=False)
    if not df_dist_s1.empty:
        df_dist_s1.to_excel(writer, sheet_name="7_S1_원시분포", index=False)
    df_dist_s2.to_excel(writer, sheet_name="8_S2_원시분포", index=False)

elapsed = time.time() - t0
size_kb = os.path.getsize(output_path) / 1024

# ══════════════════════════════════════════════
# 최종 요약
# ══════════════════════════════════════════════
print(f"\n{'='*70}")
print(f"  ★ 논문 목표 대비 결과")
print(f"{'='*70}")
print(f"  [Scenario 1] bead 2.5mm, Q=2100, p=0.5:")
print(f"    FiPLSim: μ={s1_critical['μ (MPa)']}, Pf={s1_critical['Pf (%)']}%")
print(f"    논문:    μ=0.1100,      Pf=2.43%")
print(f"  [Scenario 2] bead 2.5mm, Q=2100, p_b=0.5:")
print(f"    FiPLSim: μ={s2_critical['μ (MPa)']}, Pf={s2_critical['Pf (%)']}%")
print(f"    논문:    μ=0.1035,      Pf=12.63%")
print(f"\n{'='*70}")
print(f"  파일: {output_path}")
print(f"  크기: {size_kb:.1f} KB | 소요: {elapsed:.1f}초")
print(f"{'='*70}")
