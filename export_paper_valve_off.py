"""
논문 원본 조건 검증 — 밸브 OFF, 80A 라이저, 입구 0.4 MPa

Task 1: Case B (bead=0), 밸브 OFF → Q=1200, 2100
        + Case A (bead=2.5mm, p=0.5) → Q=2100
Task 2: 베르누이 MC p=0.1/0.3/0.7/0.9, Q=2100, bead 2.5mm, 밸브 OFF

출력: FiPLSim_논문검증_밸브OFF_데이터.xlsx
"""
import os, sys, math, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from constants import (
    DEFAULT_EQUIPMENT_K_FACTORS, PIPE_DIMENSIONS,
    get_inner_diameter_m, RHO, G, MIN_TERMINAL_PRESSURE_MPA,
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
INLET_P = 0.4       # 0.4 MPa
SUPPLY = "80A"       # 라이저 (밸브 OFF이지만 참조용)

D_supply = get_inner_diameter_m(SUPPLY)
D_supply_mm = PIPE_DIMENSIONS[SUPPLY]["id_mm"]

print("=" * 70)
print("  FiPLSim — 논문 원본 조건 (밸브 OFF, 80A, 0.4 MPa)")
print("=" * 70)
print(f"  {NUM_BR}×{HEADS} = {NUM_BR*HEADS} heads")
print(f"  입구 압력: {INLET_P} MPa | 라이저: {SUPPLY} | 밸브: OFF")
t0 = time.time()

# ══════════════════════════════════════════════
# Sheet 1: 시뮬레이션 조건
# ══════════════════════════════════════════════
df_cond = pd.DataFrame({
    "항목": [
        "가지배관 수", "가지배관당 헤드", "전체 헤드",
        "가지배관 간격 (m)", "헤드 간격 (m)", "입구 압력 (MPa)",
        "라이저 관경", "밸브 상태", "말단 최소 기준 (MPa)",
    ],
    "값": [
        NUM_BR, HEADS, NUM_BR * HEADS,
        BRANCH_SP, HEAD_SP, INLET_P,
        SUPPLY, "OFF (전체 해제)", MIN_TERMINAL_PRESSURE_MPA,
    ],
})

# ══════════════════════════════════════════════
# Sheet 2: Task 1 — 논문 Table 7 비교
# ══════════════════════════════════════════════
print("\n[1/3] Task 1: 논문 Table 7 비교...")

# 논문 목표값
paper_targets = {
    "Case B (bead=0), Q=1200": 0.3191,
    "Case B (bead=0), Q=2100": 0.1563,
    "Case A (bead=2.5mm, p=0.5), Q=2100": 0.1100,
}

task1_rows = []

# --- Case B (bead=0), Q=1200, 밸브 OFF ---
sys_1200 = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=1200.0,
)
res_1200 = calculate_dynamic_system(sys_1200, equipment_k_factors=None)
task1_rows.append({
    "조건": "Case B (bead=0)",
    "Q (LPM)": 1200,
    "밸브": "OFF",
    "FiPLSim 말단압력 (MPa)": round(res_1200["worst_terminal_mpa"], 6),
    "논문 목표값 (MPa)": 0.3191,
    "차이 (kPa)": round((res_1200["worst_terminal_mpa"] - 0.3191) * 1000, 2),
    "교차배관 손실 (kPa)": round(res_1200["cross_main_cumulative"] * 1000, 2),
    "최악 가지배관": res_1200["worst_branch_index"],
})
print(f"  Case B, Q=1200: FiPLSim={res_1200['worst_terminal_mpa']:.6f} vs 논문=0.3191 MPa "
      f"(차이: {(res_1200['worst_terminal_mpa']-0.3191)*1000:+.2f} kPa)")

# --- Case B (bead=0), Q=2100, 밸브 OFF ---
sys_2100 = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
)
res_2100 = calculate_dynamic_system(sys_2100, equipment_k_factors=None)
task1_rows.append({
    "조건": "Case B (bead=0)",
    "Q (LPM)": 2100,
    "밸브": "OFF",
    "FiPLSim 말단압력 (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "논문 목표값 (MPa)": 0.1563,
    "차이 (kPa)": round((res_2100["worst_terminal_mpa"] - 0.1563) * 1000, 2),
    "교차배관 손실 (kPa)": round(res_2100["cross_main_cumulative"] * 1000, 2),
    "최악 가지배관": res_2100["worst_branch_index"],
})
print(f"  Case B, Q=2100: FiPLSim={res_2100['worst_terminal_mpa']:.6f} vs 논문=0.1563 MPa "
      f"(차이: {(res_2100['worst_terminal_mpa']-0.1563)*1000:+.2f} kPa)")

# --- Case A (bead=2.5mm 전체, 기존 기술 최악), Q=2100, 밸브 OFF ---
beads_all = [[2.5] * HEADS for _ in range(NUM_BR)]
sys_2100_A = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
    bead_heights_2d=beads_all,
)
res_2100_A = calculate_dynamic_system(sys_2100_A, equipment_k_factors=None)
task1_rows.append({
    "조건": "Case A (bead=2.5mm, 전체)",
    "Q (LPM)": 2100,
    "밸브": "OFF",
    "FiPLSim 말단압력 (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "논문 목표값 (MPa)": 0.1100,
    "차이 (kPa)": round((res_2100_A["worst_terminal_mpa"] - 0.1100) * 1000, 2),
    "교차배관 손실 (kPa)": round(res_2100_A["cross_main_cumulative"] * 1000, 2),
    "최악 가지배관": res_2100_A["worst_branch_index"],
})
print(f"  Case A, Q=2100: FiPLSim={res_2100_A['worst_terminal_mpa']:.6f} vs 논문=0.1100 MPa "
      f"(차이: {(res_2100_A['worst_terminal_mpa']-0.1100)*1000:+.2f} kPa)")

# 비드 영향
bead_effect = (res_2100["worst_terminal_mpa"] - res_2100_A["worst_terminal_mpa"]) * 1000
print(f"\n  비드 영향 (B→A): {bead_effect:.2f} kPa 추가 손실")
print(f"  논문 비드 영향:   {(0.1563-0.1100)*1000:.1f} kPa")

df_task1 = pd.DataFrame(task1_rows)

# ══════════════════════════════════════════════
# Sheet 3: 전 유량 범위 스캔 (밸브 OFF)
# ══════════════════════════════════════════════
print("\n[2/3] 전 유량 범위 스캔...")
flows_full = [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2100]
scan_rows = []

for Q in flows_full:
    sys_q = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
    )
    # Case B (bead=0)
    res_b = calculate_dynamic_system(sys_q, equipment_k_factors=None)

    # Case A (bead=2.5mm 전체)
    beads_a = [[2.5] * HEADS for _ in range(NUM_BR)]
    sys_a = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
        bead_heights_2d=beads_a,
    )
    res_a = calculate_dynamic_system(sys_a, equipment_k_factors=None)

    scan_rows.append({
        "Q (LPM)": Q,
        "Case B 말단압력 (MPa)": round(res_b["worst_terminal_mpa"], 6),
        "Case A 말단압력 (MPa)": round(res_a["worst_terminal_mpa"], 6),
        "비드 영향 (kPa)": round((res_b["worst_terminal_mpa"] - res_a["worst_terminal_mpa"]) * 1000, 2),
        "Case B 총손실 (kPa)": round((INLET_P - res_b["worst_terminal_mpa"]) * 1000, 2),
        "Case A 총손실 (kPa)": round((INLET_P - res_a["worst_terminal_mpa"]) * 1000, 2),
        "Case B Pass/Fail": "PASS" if res_b["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else "FAIL",
        "Case A Pass/Fail": "PASS" if res_a["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else "FAIL",
    })

df_scan = pd.DataFrame(scan_rows)

# ══════════════════════════════════════════════
# Sheet 4: Task 2 — 베르누이 MC (밸브 OFF)
# ══════════════════════════════════════════════
print("\n[3/3] Task 2: 베르누이 MC (밸브 OFF)...")
p_values = [0.1, 0.3, 0.7, 0.9]
Q_bern = 2100.0
BEAD_H = 2.5
N_ITER = 1000

bern_rows = []
all_pressures = {}
all_bead_counts = {}

# 기준선 p=0 (Case B)
bern_rows.append({
    "p값": 0.0,
    "μ (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "σ (MPa)": 0.0,
    "Min (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "Max (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "P5% (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "P25% (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "P50% (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "P75% (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "P95% (MPa)": round(res_2100["worst_terminal_mpa"], 6),
    "Pf (%)": 0.0 if res_2100["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else 100.0,
    "기대 비드 수": 0.0,
    "실제 평균 비드 수": 0.0,
})

for p in p_values:
    print(f"  p={p} ({N_ITER}회)...")
    res_b = run_bernoulli_monte_carlo(
        p_bead=p, n_iterations=N_ITER, bead_height_mm=BEAD_H,
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
        beads_per_branch=0, topology="tree",
        equipment_k_factors=None,   # ★ 밸브 OFF
    )

    tp = res_b["terminal_pressures"]
    all_pressures[p] = tp
    all_bead_counts[p] = res_b["bead_counts"]
    pct = np.percentile(tp, [5, 25, 50, 75, 95])

    bern_rows.append({
        "p값": p,
        "μ (MPa)": round(res_b["mean_pressure"], 6),
        "σ (MPa)": round(res_b["std_pressure"], 6),
        "Min (MPa)": round(res_b["min_pressure"], 6),
        "Max (MPa)": round(res_b["max_pressure"], 6),
        "P5% (MPa)": round(pct[0], 6),
        "P25% (MPa)": round(pct[1], 6),
        "P50% (MPa)": round(pct[2], 6),
        "P75% (MPa)": round(pct[3], 6),
        "P95% (MPa)": round(pct[4], 6),
        "Pf (%)": round(res_b["p_below_threshold"] * 100, 2),
        "기대 비드 수": round(res_b["expected_bead_count"], 1),
        "실제 평균 비드 수": round(res_b["mean_bead_count"], 1),
    })

    print(f"    μ={res_b['mean_pressure']:.6f}, σ={res_b['std_pressure']:.6f}, "
          f"Min={res_b['min_pressure']:.6f}, Max={res_b['max_pressure']:.6f}, "
          f"Pf={res_b['p_below_threshold']*100:.1f}%")

# p=1.0 (Case A 전체 비드)
bern_rows.append({
    "p값": 1.0,
    "μ (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "σ (MPa)": 0.0,
    "Min (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "Max (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "P5% (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "P25% (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "P50% (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "P75% (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "P95% (MPa)": round(res_2100_A["worst_terminal_mpa"], 6),
    "Pf (%)": 0.0 if res_2100_A["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else 100.0,
    "기대 비드 수": float(NUM_BR * HEADS),
    "실제 평균 비드 수": float(NUM_BR * HEADS),
})

df_bern = pd.DataFrame(bern_rows)

# ══════════════════════════════════════════════
# Sheet 5: MC 원시 분포
# ══════════════════════════════════════════════
dist_cols = {}
for p in p_values:
    dist_cols[f"p={p} 말단압력(MPa)"] = np.round(all_pressures[p], 6)
    dist_cols[f"p={p} 비드수"] = all_bead_counts[p]
df_dist = pd.DataFrame(dist_cols)

# ══════════════════════════════════════════════
# 엑셀 저장
# ══════════════════════════════════════════════
output_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "FiPLSim_논문검증_밸브OFF_데이터.xlsx",
)

with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    df_cond.to_excel(writer, sheet_name="1_조건", index=False)
    df_task1.to_excel(writer, sheet_name="2_논문비교_Table7", index=False)
    df_scan.to_excel(writer, sheet_name="3_전유량_CaseAB", index=False)
    df_bern.to_excel(writer, sheet_name="4_베르누이MC_요약", index=False)
    df_dist.to_excel(writer, sheet_name="5_MC원시분포_1000회", index=False)

elapsed = time.time() - t0
size_kb = os.path.getsize(output_path) / 1024
print(f"\n{'='*70}")
print(f"  파일: {output_path}")
print(f"  크기: {size_kb:.1f} KB | 소요: {elapsed:.1f}초")
print(f"{'='*70}")
