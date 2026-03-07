"""
논문값 최종 검증 + 베르누이 MC — 입구 압력 0.4 MPa 전체 데이터 엑셀 내보내기

조건:
  - 입구 압력: 0.4 MPa
  - 라이저 관경: 80A
  - 밸브: 전체 ON (K=6.20)

Task 1: Case B (bead=0), 밸브 ON, 80A, 0.4 MPa → Q=1200/1600/2100
Task 2: Bernoulli MC p=0.1/0.3/0.7/0.9, Q=2100, bead 2.5mm, 0.4 MPa

출력: FiPLSim_논문검증_0.4MPa_데이터.xlsx
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
INLET_P = 0.4       # ★ 변경: 0.4 MPa
SUPPLY = "80A"

EQUIP_K = {}
for name, info in DEFAULT_EQUIPMENT_K_FACTORS.items():
    EQUIP_K[name] = {"K": info["K"], "qty": info["qty"]}
TOTAL_K = sum(v["K"] * v["qty"] for v in EQUIP_K.values())

D_supply = get_inner_diameter_m(SUPPLY)
D_supply_mm = PIPE_DIMENSIONS[SUPPLY]["id_mm"]

print("=" * 70)
print("  FiPLSim — 논문 검증 (입구 압력 0.4 MPa)")
print("=" * 70)
print(f"  {NUM_BR} branches x {HEADS} heads = {NUM_BR*HEADS} heads")
print(f"  입구 압력: {INLET_P} MPa  ★")
print(f"  라이저: {SUPPLY} (내경 {D_supply_mm:.2f} mm)")
print(f"  밸브 K 합계: {TOTAL_K:.2f}")
t0 = time.time()

# ══════════════════════════════════════════════
# Sheet 1: 시뮬레이션 조건
# ══════════════════════════════════════════════
df_cond = pd.DataFrame({
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
})

# ══════════════════════════════════════════════
# Sheet 2: 밸브 K-factor 상세
# ══════════════════════════════════════════════
valve_rows = []
for name, info in DEFAULT_EQUIPMENT_K_FACTORS.items():
    valve_rows.append({
        "부속류": name, "영문명": info["desc"],
        "K값": info["K"], "수량": info["qty"],
        "K×수량": info["K"] * info["qty"],
    })
valve_rows.append({"부속류": "합계", "영문명": "—", "K값": "—", "수량": "—", "K×수량": TOTAL_K})
df_valve = pd.DataFrame(valve_rows)

# ══════════════════════════════════════════════
# Sheet 3: Task 1 — Case B, 밸브 ON, 80A, 0.4 MPa
# ══════════════════════════════════════════════
print("\n[1/4] Task 1: 논문 최종 검증 (0.4 MPa)...")
flows = [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2100]
task1_rows = []

for Q in flows:
    sys_obj = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
    )
    res_on = calculate_dynamic_system(sys_obj, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
    res_off = calculate_dynamic_system(sys_obj, equipment_k_factors=None)

    V = velocity_from_flow(Q, D_supply)
    valve_loss = res_on["equipment_loss_mpa"]
    pipe_loss = INLET_P - valve_loss - res_on["worst_terminal_mpa"]
    total_loss = INLET_P - res_on["worst_terminal_mpa"]
    pf = "PASS" if res_on["worst_terminal_mpa"] >= MIN_TERMINAL_PRESSURE_MPA else "FAIL"

    task1_rows.append({
        "Q (LPM)": Q,
        "라이저 유속 (m/s)": round(V, 4),
        "밸브 손실 (kPa)": round(valve_loss * 1000, 2),
        "배관 손실 (kPa)": round(pipe_loss * 1000, 2),
        "총 손실 (kPa)": round(total_loss * 1000, 2),
        "말단압력_밸브ON (MPa)": round(res_on["worst_terminal_mpa"], 6),
        "말단압력_밸브OFF (MPa)": round(res_off["worst_terminal_mpa"], 6),
        "ON-OFF 차이 (kPa)": round((res_off["worst_terminal_mpa"] - res_on["worst_terminal_mpa"]) * 1000, 2),
        "Pass/Fail": pf,
        "교차배관 손실 (kPa)": round(res_on["cross_main_cumulative"] * 1000, 2),
        "최악 가지배관": res_on["worst_branch_index"],
    })

    if Q in [1200, 1600, 2100]:
        print(f"  Q={Q:>5} LPM → 말단: {res_on['worst_terminal_mpa']:.6f} MPa, "
              f"밸브손실: {valve_loss*1000:.2f} kPa, {pf}")

df_task1 = pd.DataFrame(task1_rows)

# 논문 비교 출력
print(f"\n  ★ 논문 Table 6 참조: 0.1312 MPa @ 2,100 LPM")
sim_2100 = next(r for r in task1_rows if r["Q (LPM)"] == 2100)
print(f"  ★ FiPLSim 결과:     {sim_2100['말단압력_밸브ON (MPa)']:.6f} MPa @ 2,100 LPM")

# ══════════════════════════════════════════════
# Sheet 4: 밸브별 손실 상세 (유량별)
# ══════════════════════════════════════════════
print("\n[2/4] 밸브별 손실 상세...")
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
            "Q (LPM)": Q, "부속류": d["name"],
            "K값": d["K"], "수량": d["qty"],
            "손실 (kPa)": round(d["loss_mpa"] * 1000, 4),
        })
    detail_rows.append({
        "Q (LPM)": Q, "부속류": "합계",
        "K값": TOTAL_K, "수량": "—",
        "손실 (kPa)": round(res["equipment_loss_mpa"] * 1000, 4),
    })
df_detail = pd.DataFrame(detail_rows)

# ══════════════════════════════════════════════
# Sheet 5: Task 2 — 베르누이 MC (0.4 MPa)
# ══════════════════════════════════════════════
print("\n[3/4] Task 2: 베르누이 MC (0.4 MPa)...")
p_values = [0.1, 0.3, 0.7, 0.9]
Q_bern = 2100.0
BEAD_H = 2.5
N_ITER = 1000

bern_rows = []
all_pressures = {}
all_bead_counts = {}

# 기준선 (p=0)
sys_b0 = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
)
res_b0 = calculate_dynamic_system(sys_b0, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
p0_term = res_b0["worst_terminal_mpa"]

bern_rows.append({
    "p값": 0.0,
    "μ (MPa)": round(p0_term, 6), "σ (MPa)": 0.0,
    "Min (MPa)": round(p0_term, 6), "Max (MPa)": round(p0_term, 6),
    "P5% (MPa)": round(p0_term, 6), "P25% (MPa)": round(p0_term, 6),
    "P50% (MPa)": round(p0_term, 6), "P75% (MPa)": round(p0_term, 6),
    "P95% (MPa)": round(p0_term, 6),
    "Pf (%)": 0.0 if p0_term >= MIN_TERMINAL_PRESSURE_MPA else 100.0,
    "기대 비드 수": 0.0, "실제 평균 비드 수": 0.0,
})

for p in p_values:
    print(f"  p={p} ({N_ITER}회)...")
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

# p=1.0 (최악)
beads_all = [[BEAD_H] * HEADS for _ in range(NUM_BR)]
sys_b1 = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
    bead_heights_2d=beads_all,
)
res_b1 = calculate_dynamic_system(sys_b1, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
p1_term = res_b1["worst_terminal_mpa"]

bern_rows.append({
    "p값": 1.0,
    "μ (MPa)": round(p1_term, 6), "σ (MPa)": 0.0,
    "Min (MPa)": round(p1_term, 6), "Max (MPa)": round(p1_term, 6),
    "P5% (MPa)": round(p1_term, 6), "P25% (MPa)": round(p1_term, 6),
    "P50% (MPa)": round(p1_term, 6), "P75% (MPa)": round(p1_term, 6),
    "P95% (MPa)": round(p1_term, 6),
    "Pf (%)": 0.0 if p1_term >= MIN_TERMINAL_PRESSURE_MPA else 100.0,
    "기대 비드 수": float(NUM_BR * HEADS), "실제 평균 비드 수": float(NUM_BR * HEADS),
})

df_bern = pd.DataFrame(bern_rows)

# ══════════════════════════════════════════════
# Sheet 6: MC 원시 분포
# ══════════════════════════════════════════════
print("\n[4/4] 원시 분포 정리...")
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
    "FiPLSim_논문검증_0.4MPa_데이터.xlsx",
)

with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    df_cond.to_excel(writer, sheet_name="1_시뮬레이션조건", index=False)
    df_valve.to_excel(writer, sheet_name="2_밸브K상세", index=False)
    df_task1.to_excel(writer, sheet_name="3_논문검증_CaseB", index=False)
    df_detail.to_excel(writer, sheet_name="4_밸브별손실_유량별", index=False)
    df_bern.to_excel(writer, sheet_name="5_베르누이MC_요약", index=False)
    df_dist.to_excel(writer, sheet_name="6_MC원시분포_1000회", index=False)

elapsed = time.time() - t0
size_kb = os.path.getsize(output_path) / 1024
print(f"\n{'='*70}")
print(f"  파일 생성 완료: {output_path}")
print(f"  크기: {size_kb:.1f} KB | 소요: {elapsed:.1f}초")
print(f"{'='*70}")
