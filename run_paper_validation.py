"""
논문값 최종 검증 + 베르누이 MC p값 민감도 시뮬레이션

Task 1: Case B (bead=0), 밸브 ON, 80A → Q=1200/1600/2100 LPM 말단 압력
Task 2: Bernoulli MC (p=0.1/0.3/0.7/0.9), Q=2100, bead 2.5mm, 밸브 ON, 80A
"""
import os, sys, math
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from constants import (
    DEFAULT_EQUIPMENT_K_FACTORS, PIPE_DIMENSIONS,
    get_inner_diameter_m, RHO, G, MIN_TERMINAL_PRESSURE_MPA,
)
from hydraulics import velocity_from_flow
from pipe_network import (
    generate_dynamic_system, calculate_dynamic_system,
    compare_dynamic_cases,
)
from simulation import run_bernoulli_monte_carlo

# ══════════════════════════════════════════════
# 공통 설정
# ══════════════════════════════════════════════
NUM_BR = 4
HEADS = 8
BRANCH_SP = 3.5
HEAD_SP = 2.3
INLET_P = 1.4       # MPa (기본 입구 압력)
SUPPLY = "80A"       # 라이저 관경

# 밸브 전체 ON
EQUIP_K = {}
for name, info in DEFAULT_EQUIPMENT_K_FACTORS.items():
    EQUIP_K[name] = {"K": info["K"], "qty": info["qty"]}
TOTAL_K = sum(v["K"] * v["qty"] for v in EQUIP_K.values())

D_supply = get_inner_diameter_m(SUPPLY)
D_supply_mm = PIPE_DIMENSIONS[SUPPLY]["id_mm"]

print("=" * 70)
print("  FiPLSim — 논문값 최종 검증 시뮬레이션")
print("=" * 70)
print(f"  공통 조건: {NUM_BR} branches x {HEADS} heads = {NUM_BR*HEADS} heads")
print(f"  입구 압력: {INLET_P} MPa")
print(f"  라이저 관경: {SUPPLY} (내경 {D_supply_mm:.2f} mm)")
print(f"  밸브 등가 K 합계: {TOTAL_K:.2f} (6종 전체 ON)")
print(f"  가지배관 간격: {BRANCH_SP} m, 헤드 간격: {HEAD_SP} m")


# ══════════════════════════════════════════════
# Task 1: 논문값 최종 검증 (Case B, 밸브 ON, 80A)
# ══════════════════════════════════════════════
print("\n" + "=" * 70)
print("  [Task 1] 논문값 최종 검증 — Case B (bead=0), 밸브 ON, 80A")
print("=" * 70)

flows_task1 = [1200, 1600, 2100]

# 헤더
print(f"\n  {'Q (LPM)':>10} | {'V_supply (m/s)':>14} | {'밸브 손실 (kPa)':>14} | "
      f"{'배관 손실 (kPa)':>14} | {'말단 압력 (MPa)':>14} | {'Pass/Fail':>9}")
print("  " + "-" * 90)

for Q in flows_task1:
    sys_obj = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
        # Case B: bead=0 (신기술, 비드 없음)
    )

    # 밸브 ON
    res = calculate_dynamic_system(
        sys_obj,
        equipment_k_factors=EQUIP_K,
        supply_pipe_size=SUPPLY,
    )

    # 밸브 OFF (비교용)
    res_no = calculate_dynamic_system(sys_obj, equipment_k_factors=None)

    V_supply = velocity_from_flow(Q, D_supply)
    valve_loss_kpa = res["equipment_loss_mpa"] * 1000
    pipe_loss_kpa = (INLET_P - res["equipment_loss_mpa"] - res["worst_terminal_mpa"]) * 1000
    terminal = res["worst_terminal_mpa"]
    pass_fail = "PASS" if terminal >= MIN_TERMINAL_PRESSURE_MPA else "FAIL"

    print(f"  {Q:>10,} | {V_supply:>14.4f} | {valve_loss_kpa:>14.2f} | "
          f"{pipe_loss_kpa:>14.2f} | {terminal:>14.6f} | {pass_fail:>9}")

# 논문 Table 6 비교
print(f"\n  --- 논문 Table 6 참조값: 0.1312 MPa @ 2,100 LPM ---")

# 밸브별 상세 (Q=2100)
print(f"\n  [밸브별 손실 상세 — Q=2100 LPM, 80A]")
sys_2100 = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=2100.0,
)
res_2100 = calculate_dynamic_system(sys_2100, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)

print(f"\n  {'부속류':<20} | {'K':>6} | {'수량':>4} | {'손실 (kPa)':>12}")
print("  " + "-" * 50)
for d in res_2100["equipment_loss_details"]:
    print(f"  {d['name']:<20} | {d['K']:>6.2f} | {d['qty']:>4} | {d['loss_mpa']*1000:>12.2f}")
print("  " + "-" * 50)
print(f"  {'합계':<20} | {TOTAL_K:>6.2f} |    — | {res_2100['equipment_loss_mpa']*1000:>12.2f}")

# 밸브 ON vs OFF 비교
print(f"\n  [밸브 ON/OFF 비교]")
print(f"  {'Q (LPM)':>10} | {'밸브 OFF (MPa)':>14} | {'밸브 ON (MPa)':>14} | {'차이 (kPa)':>12}")
print("  " + "-" * 60)
for Q in flows_task1:
    sys_q = generate_dynamic_system(
        num_branches=NUM_BR, heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P, total_flow_lpm=float(Q),
    )
    r_off = calculate_dynamic_system(sys_q, equipment_k_factors=None)
    r_on = calculate_dynamic_system(sys_q, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
    diff = (r_off["worst_terminal_mpa"] - r_on["worst_terminal_mpa"]) * 1000
    print(f"  {Q:>10,} | {r_off['worst_terminal_mpa']:>14.6f} | {r_on['worst_terminal_mpa']:>14.6f} | {diff:>12.2f}")


# ══════════════════════════════════════════════
# Task 2: 베르누이 MC p값 민감도
#   Scenario 1: Q=2100, bead_h=2.5mm, 밸브 ON, 80A
# ══════════════════════════════════════════════
print("\n\n" + "=" * 70)
print("  [Task 2] 베르누이 MC p값 민감도 — Q=2100, bead 2.5mm, 밸브 ON, 80A")
print("=" * 70)

p_values = [0.1, 0.3, 0.7, 0.9]
Q_bern = 2100.0
BEAD_H = 2.5
N_ITER = 1000

print(f"\n  조건: Q={Q_bern:.0f} LPM, bead_h={BEAD_H} mm, 밸브 ON (K={TOTAL_K:.2f})")
print(f"         라이저 {SUPPLY} (내경 {D_supply_mm:.2f} mm), MC {N_ITER}회 반복")
print(f"         기준: 말단 압력 < {MIN_TERMINAL_PRESSURE_MPA} MPa → 부적합(Pf)")
print()

# 헤더
print(f"  {'p값':>6} | {'μ (MPa)':>10} | {'σ (MPa)':>10} | {'Min (MPa)':>10} | "
      f"{'Max (MPa)':>10} | {'Pf (%)':>8} | {'기대 비드 수':>10} | {'실제 평균':>10}")
print("  " + "-" * 95)

bern_results = []
for p in p_values:
    res_b = run_bernoulli_monte_carlo(
        p_bead=p,
        n_iterations=N_ITER,
        bead_height_mm=BEAD_H,
        num_branches=NUM_BR,
        heads_per_branch=HEADS,
        branch_spacing_m=BRANCH_SP,
        head_spacing_m=HEAD_SP,
        inlet_pressure_mpa=INLET_P,
        total_flow_lpm=Q_bern,
        beads_per_branch=0,    # 직관 용접 비드는 제외 (이음쇠 비드만)
        topology="tree",
        equipment_k_factors=EQUIP_K,
        supply_pipe_size=SUPPLY,
    )

    mu = res_b["mean_pressure"]
    sigma = res_b["std_pressure"]
    mn = res_b["min_pressure"]
    mx = res_b["max_pressure"]
    pf = res_b["p_below_threshold"] * 100
    exp_beads = res_b["expected_bead_count"]
    actual_beads = res_b["mean_bead_count"]

    bern_results.append(res_b)

    print(f"  {p:>6.1f} | {mu:>10.6f} | {sigma:>10.6f} | {mn:>10.6f} | "
          f"{mx:>10.6f} | {pf:>8.1f} | {exp_beads:>10.1f} | {actual_beads:>10.1f}")

# 추가 분석: 기준선 (p=0, 비드 없음)
print(f"\n  --- 기준선 (p=0, 비드 없음, 밸브 ON) ---")
sys_base = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
)
res_base = calculate_dynamic_system(sys_base, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
print(f"  Case B (bead=0) 말단 압력: {res_base['worst_terminal_mpa']:.6f} MPa")
print(f"  밸브 손실: {res_base['equipment_loss_mpa']*1000:.2f} kPa")

# p=1.0 (모든 이음쇠에 비드)
print(f"\n  --- 최악 케이스 (p=1.0, 모든 이음쇠 비드 2.5mm, 밸브 ON) ---")
beads_all = [[BEAD_H] * HEADS for _ in range(NUM_BR)]
sys_worst = generate_dynamic_system(
    num_branches=NUM_BR, heads_per_branch=HEADS,
    branch_spacing_m=BRANCH_SP, head_spacing_m=HEAD_SP,
    inlet_pressure_mpa=INLET_P, total_flow_lpm=Q_bern,
    bead_heights_2d=beads_all,
)
res_worst = calculate_dynamic_system(sys_worst, equipment_k_factors=EQUIP_K, supply_pipe_size=SUPPLY)
print(f"  Case A (bead=2.5mm 전체) 말단 압력: {res_worst['worst_terminal_mpa']:.6f} MPa")
print(f"  밸브 손실: {res_worst['equipment_loss_mpa']*1000:.2f} kPa")
delta_bead = (res_base['worst_terminal_mpa'] - res_worst['worst_terminal_mpa']) * 1000
print(f"  비드 영향 (p=0 vs p=1): {delta_bead:.2f} kPa")

print(f"\n{'='*70}")
print(f"  시뮬레이션 완료")
print(f"{'='*70}")
