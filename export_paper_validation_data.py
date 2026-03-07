"""
논문값 최종 검증 + 베르누이 MC — 전체 데이터 엑셀 내보내기

출력: FiPLSim_논문검증_데이터.xlsx (시트 6개)
"""
import os, sys, math
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
INLET_P = 1.4
SUPPLY = "80A"

EQUIP_K = {}
for name, info in DEFAULT_EQUIPMENT_K_FACTORS.items():
    EQUIP_K[name] = {"K": info["K"], "qty": info["qty"]}
TOTAL_K = sum(v["K"] * v["qty"] for v in EQUIP_K.values())

D_supply = get_inner_diameter_m(SUPPLY)
D_supply_mm = PIPE_DIMENSIONS[SUPPLY]["id_mm"]

print("데이터 수집 중...")

# ══════════════════════════════════════════════
# Sheet 1: 시뮬레이션 조건
# ══════════════════════════════════════════════
cond_data = {
    "항목": [
        "가지배관 수 (n)", "가지배관당 헤드 (m)", "전체 헤드 수",
        "가지배관 간격 (m)", "헤드 간격 (m)", "입구 압력 (MPa)",
        "라이저 관경", "라이저 내경 (mm)", "밸브 등가 K 합계",
        "밸브 상태", "말단 최소 기준 (MPa)",
    ],
    "값": [
        NUM_BR, HEADS, NUM_BR * HEADS,
        BRANCH_SP, HEAD_SP, INLET_P,
        SUPPLY, D_supply_mm, TOTAL_K,
        "전체 ON (6종)", MIN_TERMINAL_PRESSURE_MPA,
    ],
}
df_cond = pd.DataFrame(cond_data)

# ══════════════════════════════════════════════
# Sheet 2: 밸브 K-factor 상세
# ══════════════════════════════════════════════
valve_rows = []
for name, info in DEFAULT_EQUIPMENT_K_FACTORS.items():
    valve_rows.append({
        "부속류": name,
        "영문명": info["desc"],
        "K값": info["K"],
        "수량": info["qty"],
        "K×수량": info["K"] * info["qty"],
    })
valve_rows.append({
    "부속류": "합계", "영문명": "—",
    "K값": "—", "수량": "—", "K×수량": TOTAL_K,
})
df_valve = pd.DataFrame(valve_rows)

# ══════════════════════════════════════════════
# Sheet 3: Task 1 — 논문값 최종 검증 (Case B, 밸브 ON, 80A)
# ══════════════════════════════════════════════
print("[1/4] Task 1: 논문값 최종 검증...")
flows = [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2100]
task1_rows = []

for Q in flows:
    sys_obj = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
    )
    # Case B (bead=0), 밸브 ON
    res_on = calculate_dynamic_system(sys_obj, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
    # Case B (bead=0), 밸브 OFF
    res_off = calculate_dynamic_system(sys_obj, equipment_k_factors=None)

    V = velocity_from_flow(Q, D_supply)
    valve_loss = res_on["equipment_loss_mpa"]
    pipe_loss = INLET_P - valve_loss - res_on["worst_terminal_mpa"]
    total_loss = INLET_P - res_on["worst_terminal_mpa"]

    task1_rows.append({
        "Q (LPM)": Q,
        "라이저 유속 (m/s)": round(V, 4),
        "밸브 손실 (kPa)": round(valve_loss * 1000, 2),
        "배관 손실 (kPa)": round(pipe_loss * 1000, 2),
        "총 손실 (kPa)": round(total_loss * 1000, 2),
        "말단 압력_밸브ON (MPa)": round(res_on["worst_terminal_mpa"], 6),
        "말단 압력_밸브OFF (MPa)": round(res_off["worst_terminal_mpa"], 6),
        "밸브 ON-OFF 차이 (kPa)": round((res_off["worst_terminal_mpa"] - res_on["worst_terminal_mpa"]) * 1000, 2),
        "Pass/Fail": "PASS" if res_on["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else "FAIL",
        "교차배관 손실 (kPa)": round(res_on["cross_main_cumulative"] * 1000, 2),
        "최악 가지배관": res_on["worst_branch_index"],
    })

df_task1 = pd.DataFrame(task1_rows)

# ══════════════════════════════════════════════
# Sheet 4: 밸브별 손실 상세 (유량별)
# ══════════════════════════════════════════════
print("[2/4] 밸브별 손실 상세...")
detail_rows = []
for Q in flows:
    sys_obj = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
    )
    res = calculate_dynamic_system(sys_obj, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
    for d in res["equipment_loss_details"]:
        detail_rows.append({
            "Q (LPM)": Q,
            "부속류": d["name"],
            "K값": d["K"],
            "수량": d["qty"],
            "손실 (kPa)": round(d["loss_mpa"] * 1000, 4),
        })
    detail_rows.append({
        "Q (LPM)": Q,
        "부속류": "합계",
        "K값": TOTAL_K,
        "수량": "—",
        "손실 (kPa)": round(res["equipment_loss_mpa"] * 1000, 4),
    })

df_detail = pd.DataFrame(detail_rows)

# ══════════════════════════════════════════════
# Sheet 5: Task 2 — 베르누이 MC p값 민감도 (요약)
# ══════════════════════════════════════════════
print("[3/4] Task 2: 베르누이 MC 민감도...")
p_values = [0.1, 0.3, 0.5, 0.7, 0.9]
Q_bern = 2100.0
BEAD_H = 2.5
N_ITER = 1000

bern_summary_rows = []
all_pressures = {}  # p값별 전체 분포 저장

for p in p_values:
    print(f"  p={p} 실행 중 ({N_ITER}회)...")
    res_b = run_bernoulli_monte_carlo(
        p_bead=p, n_iterations=N_ITER, bead_height_mm=BEAD_H,
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
        beads_per_branch=0, topology="tree",
        equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY,
    )

    tp = res_b["terminal_pressures"]
    all_pressures[p] = tp

    # 백분위수 계산
    pctiles = np.percentile(tp, [1, 5, 10, 25, 50, 75, 90, 95, 99])

    bern_summary_rows.append({
        "p값": p,
        "μ (MPa)": round(res_b["mean_pressure"], 6),
        "σ (MPa)": round(res_b["std_pressure"], 6),
        "Min (MPa)": round(res_b["min_pressure"], 6),
        "Max (MPa)": round(res_b["max_pressure"], 6),
        "P1% (MPa)": round(pctiles[0], 6),
        "P5% (MPa)": round(pctiles[1], 6),
        "P10% (MPa)": round(pctiles[2], 6),
        "P25% (MPa)": round(pctiles[3], 6),
        "P50% (MPa)": round(pctiles[4], 6),
        "P75% (MPa)": round(pctiles[5], 6),
        "P90% (MPa)": round(pctiles[6], 6),
        "P95% (MPa)": round(pctiles[7], 6),
        "P99% (MPa)": round(pctiles[8], 6),
        "Pf (%)": round(res_b["p_below_threshold"] * 100, 2),
        "기대 비드 수": round(res_b["expected_bead_count"], 1),
        "실제 평균 비드 수": round(res_b["mean_bead_count"], 1),
    })

# 기준선 (p=0)
sys_base = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
)
res_base = calculate_dynamic_system(sys_base, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
bern_summary_rows.insert(0, {
    "p값": 0.0,
    "μ (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "σ (MPa)": 0.0,
    "Min (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "Max (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P1% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P5% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P10% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P25% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P50% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P75% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P90% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P95% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "P99% (MPa)": round(res_base["worst_terminal_mpa"], 6),
    "Pf (%)": 0.0,
    "기대 비드 수": 0.0,
    "실제 평균 비드 수": 0.0,
})

# p=1.0 (최악)
beads_all = [[BEAD_H] * HEADS for _ in range(NUM_BR)]
sys_worst = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
    bead_heights_2d=beads_all,
)
res_worst = calculate_dynamic_system(sys_worst, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
bern_summary_rows.append({
    "p값": 1.0,
    "μ (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "σ (MPa)": 0.0,
    "Min (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "Max (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P1% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P5% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P10% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P25% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P50% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P75% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P90% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P95% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "P99% (MPa)": round(res_worst["worst_terminal_mpa"], 6),
    "Pf (%)": 0.0 if res_worst["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else 100.0,
    "기대 비드 수": NUM_BR * HEADS,
    "실제 평균 비드 수": NUM_BR * HEADS,
})

df_bern = pd.DataFrame(bern_summary_rows)

# ══════════════════════════════════════════════
# Sheet 6: 베르누이 MC 원시 분포 (각 p값별 1000개 말단 압력)
# ══════════════════════════════════════════════
print("[4/4] 원시 분포 데이터 정리...")
dist_data = {}
for p, tp in all_pressures.items():
    dist_data[f"p={p}_압력(MPa)"] = np.round(tp, 6)
    dist_data[f"p={p}_비드수"] = None  # placeholder

# 비드 수도 포함
for p in p_values:
    res_b2 = run_bernoulli_monte_carlo(
        p_bead=p, n_iterations=N_ITER, bead_height_mm=BEAD_H,
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
        beads_per_branch=0, topology="tree",
        equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY,
    )
    dist_data[f"p={p}_압력(MPa)"] = np.round(res_b2["terminal_pressures"], 6)
    dist_data[f"p={p}_비드수"] = res_b2["bead_counts"]

# 각 열 길이가 동일하도록 DataFrame 생성
max_len = N_ITER
dist_cols = {}
for p in p_values:
    dist_cols[f"p={p} 말단압력(MPa)"] = dist_data[f"p={p}_압력(MPa)"]
    dist_cols[f"p={p} 비드수"] = dist_data[f"p={p}_비드수"]

df_dist = pd.DataFrame(dist_cols)

# ══════════════════════════════════════════════
# 엑셀 저장
# ══════════════════════════════════════════════
output_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "FiPLSim_논문검증_데이터.xlsx",
)

print(f"\n엑셀 파일 저장 중...")
with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    df_cond.to_excel(writer, sheet_name="1_시뮬레이션조건", index=False)
    df_valve.to_excel(writer, sheet_name="2_밸브K상세", index=False)
    df_task1.to_excel(writer, sheet_name="3_논문검증_CaseB", index=False)
    df_detail.to_excel(writer, sheet_name="4_밸브별손실_유량별", index=False)
    df_bern.to_excel(writer, sheet_name="5_베르누이MC_요약", index=False)
    df_dist.to_excel(writer, sheet_name="6_MC원시분포_1000회", index=False)

size_kb = os.path.getsize(output_path) / 1024
print(f"\n  파일 생성 완료: {output_path}")
print(f"  파일 크기: {size_kb:.1f} KB")
print(f"  시트 목록:")
print(f"    1_시뮬레이션조건    — 공통 파라미터")
print(f"    2_밸브K상세        — 6종 밸브 K값/수량")
print(f"    3_논문검증_CaseB   — Q별 말단 압력 (밸브 ON/OFF)")
print(f"    4_밸브별손실_유량별  — 유량별 각 밸브 개별 손실")
print(f"    5_베르누이MC_요약   — p값별 통계 (μ,σ,Min,Max,백분위)")
print(f"    6_MC원시분포_1000회 — p값별 1000회 말단압력+비드수 원시 데이터")
