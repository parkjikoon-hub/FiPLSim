# Valve/Equipment Minor Loss Verification Tests
# ASCII-only print statements for Windows compatibility
import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {msg}")
    else:
        FAIL += 1
        print(f"  [FAIL] {msg}")


# ══════════════════════════════════════════════
#  Test 1: Valve K-factor constants check
# ══════════════════════════════════════════════
print("\n[1] constants.py - valve K-factors")
from constants import (
    DEFAULT_EQUIPMENT_K_FACTORS, DEFAULT_SUPPLY_PIPE_SIZE,
    PIPE_DIMENSIONS, get_inner_diameter_m, G, RHO,
)

# 1a: 6 types of valves exist
check(len(DEFAULT_EQUIPMENT_K_FACTORS) == 6, "6 valve types defined")

# 1b: Total equivalent K = 6.20
total_K = sum(v["K"] * v["qty"] for v in DEFAULT_EQUIPMENT_K_FACTORS.values())
check(abs(total_K - 6.20) < 0.01, f"Total equiv K = {total_K:.2f} (expected 6.20)")

# 1c: Default supply pipe = 100A
check(DEFAULT_SUPPLY_PIPE_SIZE == "100A", "Default supply pipe = 100A")

# 1d: Each valve has K, qty, desc
for name, info in DEFAULT_EQUIPMENT_K_FACTORS.items():
    check("K" in info and "qty" in info and "desc" in info,
          f"'{name}' has K/qty/desc fields")


# ══════════════════════════════════════════════
#  Test 2: Valve ON/OFF terminal pressure difference
# ══════════════════════════════════════════════
print("\n[2] Valve ON/OFF - terminal pressure difference")
from pipe_network import generate_dynamic_system, calculate_dynamic_system

sys_base = generate_dynamic_system(
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
)

# 2a: WITHOUT valves (equipment_k_factors=None)
res_no_valve = calculate_dynamic_system(sys_base, equipment_k_factors=None)
p_no_valve = res_no_valve["worst_terminal_mpa"]
eq_loss_no = res_no_valve["equipment_loss_mpa"]
check(eq_loss_no == 0.0, f"No-valve: equipment_loss = {eq_loss_no:.6f} MPa (expected 0)")

# 2b: WITH valves (all 6 types)
res_with_valve = calculate_dynamic_system(
    sys_base,
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)
p_with_valve = res_with_valve["worst_terminal_mpa"]
eq_loss_on = res_with_valve["equipment_loss_mpa"]

check(eq_loss_on > 0.0, f"With-valve: equipment_loss = {eq_loss_on:.6f} MPa (> 0)")
check(p_with_valve < p_no_valve, f"Terminal: {p_with_valve:.4f} < {p_no_valve:.4f} (valve reduces pressure)")

delta_kpa = (p_no_valve - p_with_valve) * 1000
check(abs(delta_kpa - eq_loss_on * 1000) < 0.01,
      f"Delta = {delta_kpa:.2f} kPa == equipment_loss {eq_loss_on*1000:.2f} kPa")

# 2c: Verify equipment_loss_details
details = res_with_valve["equipment_loss_details"]
check(len(details) == 6, f"equipment_loss_details has {len(details)} entries (expected 6)")
detail_sum = sum(d["loss_mpa"] for d in details)
check(abs(detail_sum - eq_loss_on) < 1e-5, f"Details sum = {detail_sum:.6f} ~= total {eq_loss_on:.6f} (rounding tolerance)")


# ══════════════════════════════════════════════
#  Test 3: Q-squared proportional pattern (200~1600 LPM)
# ══════════════════════════════════════════════
print("\n[3] Q-squared proportional pattern (200~1600 LPM)")
from hydraulics import velocity_from_flow, minor_loss, head_to_mpa

supply_id_m = get_inner_diameter_m("100A")

# Manual calculation for reference flows
flows = [200, 400, 600, 800, 1000, 1200, 1400, 1600]
losses_manual = []
losses_code = []

for Q in flows:
    # Manual: h = sum(K*qty) * V^2/(2g), V = Q/A
    V = velocity_from_flow(Q, supply_id_m)
    h_total = 0.0
    for info in DEFAULT_EQUIPMENT_K_FACTORS.values():
        h_total += info["K"] * info["qty"] * (V ** 2 / (2 * G))
    loss_manual_mpa = RHO * G * h_total / 1e6
    losses_manual.append(loss_manual_mpa)

    # Code path
    sys_q = generate_dynamic_system(
        num_branches=4, heads_per_branch=8,
        inlet_pressure_mpa=1.4, total_flow_lpm=float(Q),
    )
    res_q = calculate_dynamic_system(
        sys_q,
        equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
        supply_pipe_size="100A",
    )
    losses_code.append(res_q["equipment_loss_mpa"])

# 3a: Manual vs code match
for i, Q in enumerate(flows):
    diff = abs(losses_manual[i] - losses_code[i])
    check(diff < 1e-6, f"Q={Q} LPM: manual={losses_manual[i]:.6f} vs code={losses_code[i]:.6f}")

# 3b: Q^2 proportionality check
# loss(Q2)/loss(Q1) should == (Q2/Q1)^2
ref_Q = 200
ref_loss = losses_code[0]  # Q=200 loss

print("  -- Q^2 proportionality ratios --")
for i, Q in enumerate(flows):
    if ref_loss > 0:
        actual_ratio = losses_code[i] / ref_loss
        expected_ratio = (Q / ref_Q) ** 2
        pct_err = abs(actual_ratio - expected_ratio) / expected_ratio * 100
        check(pct_err < 0.1, f"Q={Q}: ratio={actual_ratio:.2f} vs expected={expected_ratio:.2f} (err {pct_err:.3f}%)")


# ══════════════════════════════════════════════
#  Test 4: Supply pipe size comparison (50A ~ 100A)
# ══════════════════════════════════════════════
print("\n[4] Supply pipe size comparison (50A ~ 100A)")

sizes = ["50A", "65A", "80A", "100A"]
losses_by_size = {}
Q_test = 1200  # LPM

sys_size_test = generate_dynamic_system(
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=float(Q_test),
)

for size in sizes:
    res_s = calculate_dynamic_system(
        sys_size_test,
        equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
        supply_pipe_size=size,
    )
    losses_by_size[size] = res_s["equipment_loss_mpa"]
    print(f"  -- {size}: valve loss = {losses_by_size[size]*1000:.2f} kPa")

# 4a: Smaller pipe = higher loss (monotonic decrease as size increases)
check(losses_by_size["50A"] > losses_by_size["65A"], "50A loss > 65A loss")
check(losses_by_size["65A"] > losses_by_size["80A"], "65A loss > 80A loss")
check(losses_by_size["80A"] > losses_by_size["100A"], "80A loss > 100A loss")

# 4b: 100A at Q=1200 should be ~18 kPa (from memory)
loss_100A_kpa = losses_by_size["100A"] * 1000
check(15 < loss_100A_kpa < 25, f"100A@1200LPM: {loss_100A_kpa:.2f} kPa (expected 15~25)")

# 4c: 80A at Q=1200 should be ~54 kPa (from memory)
loss_80A_kpa = losses_by_size["80A"] * 1000
check(40 < loss_80A_kpa < 70, f"80A@1200LPM: {loss_80A_kpa:.2f} kPa (expected 40~70)")

# 4d: Verify D^(-4) proportionality between sizes
# loss ratio should approximately equal (D1/D2)^(-4)
D_100 = PIPE_DIMENSIONS["100A"]["id_mm"]
D_80 = PIPE_DIMENSIONS["80A"]["id_mm"]
expected_ratio_80_100 = (D_100 / D_80) ** 4
actual_ratio_80_100 = losses_by_size["80A"] / losses_by_size["100A"]
pct_err = abs(actual_ratio_80_100 - expected_ratio_80_100) / expected_ratio_80_100 * 100
check(pct_err < 0.1, f"80A/100A loss ratio: actual={actual_ratio_80_100:.2f} vs D^4={expected_ratio_80_100:.2f}")


# ══════════════════════════════════════════════
#  Test 5: Empty valve dict = no loss
# ══════════════════════════════════════════════
print("\n[5] Edge cases")

# 5a: Empty dict
res_empty = calculate_dynamic_system(sys_base, equipment_k_factors={})
check(res_empty["equipment_loss_mpa"] == 0.0, "Empty dict: loss = 0")
check(len(res_empty["equipment_loss_details"]) == 0, "Empty dict: no details")

# 5b: Single valve only
single_valve = {"Gate Valve": {"K": 0.15, "qty": 1}}
res_single = calculate_dynamic_system(
    sys_base, equipment_k_factors=single_valve, supply_pipe_size="100A",
)
check(res_single["equipment_loss_mpa"] > 0, "Single valve: loss > 0")
check(len(res_single["equipment_loss_details"]) == 1, "Single valve: 1 detail entry")

# 5c: Partial valve set (3 of 6)
partial = {
    k: v for i, (k, v) in enumerate(DEFAULT_EQUIPMENT_K_FACTORS.items()) if i < 3
}
res_partial = calculate_dynamic_system(
    sys_base, equipment_k_factors=partial, supply_pipe_size="100A",
)
check(0 < res_partial["equipment_loss_mpa"] < eq_loss_on,
      f"Partial (3/6): loss={res_partial['equipment_loss_mpa']*1000:.2f} kPa < full={eq_loss_on*1000:.2f} kPa")


# ══════════════════════════════════════════════
#  Test 6: compare_dynamic_cases with equipment params
# ══════════════════════════════════════════════
print("\n[6] compare_dynamic_cases with equipment_k_factors")
from pipe_network import compare_dynamic_cases

res_cmp_no = compare_dynamic_cases(
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    equipment_k_factors=None,
)
res_cmp_yes = compare_dynamic_cases(
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)

check(res_cmp_yes["terminal_A_mpa"] < res_cmp_no["terminal_A_mpa"],
      "Case A with valve < without valve")
check(res_cmp_yes["terminal_B_mpa"] < res_cmp_no["terminal_B_mpa"],
      "Case B with valve < without valve")

# Both cases should see same equipment loss amount
delta_A = res_cmp_no["terminal_A_mpa"] - res_cmp_yes["terminal_A_mpa"]
delta_B = res_cmp_no["terminal_B_mpa"] - res_cmp_yes["terminal_B_mpa"]
check(abs(delta_A - delta_B) < 1e-6,
      f"Valve loss is same for Case A ({delta_A*1000:.2f} kPa) and B ({delta_B*1000:.2f} kPa)")


# ══════════════════════════════════════════════
#  Test 7: Topology routing (tree) with valves
# ══════════════════════════════════════════════
print("\n[7] compare_dynamic_cases_with_topology - tree mode + valves")
from pipe_network import compare_dynamic_cases_with_topology

res_topo_no = compare_dynamic_cases_with_topology(
    topology="tree", num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    equipment_k_factors=None,
)
res_topo_yes = compare_dynamic_cases_with_topology(
    topology="tree", num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)

check(res_topo_yes["terminal_A_mpa"] < res_topo_no["terminal_A_mpa"],
      "Topology tree: valve reduces terminal A")
check(res_topo_yes["terminal_B_mpa"] < res_topo_no["terminal_B_mpa"],
      "Topology tree: valve reduces terminal B")


# ══════════════════════════════════════════════
#  Test 8: Monte Carlo propagation
# ══════════════════════════════════════════════
print("\n[8] Monte Carlo - valve parameter propagation")
from simulation import run_dynamic_monte_carlo

mc_no = run_dynamic_monte_carlo(
    n_iterations=30, bead_height_mm=1.5,
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=None,
)
mc_yes = run_dynamic_monte_carlo(
    n_iterations=30, bead_height_mm=1.5,
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)

check(mc_yes["mean_pressure"] < mc_no["mean_pressure"],
      f"MC mean: valve={mc_yes['mean_pressure']:.4f} < no-valve={mc_no['mean_pressure']:.4f}")
check(mc_yes["min_pressure"] < mc_no["min_pressure"],
      f"MC min: valve={mc_yes['min_pressure']:.4f} < no-valve={mc_no['min_pressure']:.4f}")

# MC: valve loss should shift entire distribution by a constant amount
shift = mc_no["mean_pressure"] - mc_yes["mean_pressure"]
print(f"  -- MC mean shift = {shift*1000:.2f} kPa (expected ~= valve loss)")
# The shift should be approximately equal to the static valve loss
check(abs(shift - eq_loss_on) < 0.002,
      f"MC shift {shift*1000:.2f} kPa ~= static valve loss {eq_loss_on*1000:.2f} kPa (tolerance 2 kPa)")


# ══════════════════════════════════════════════
#  Test 9: Sensitivity analysis propagation
# ══════════════════════════════════════════════
print("\n[9] Sensitivity analysis - valve parameter propagation")
from simulation import run_dynamic_sensitivity

sens_no = run_dynamic_sensitivity(
    bead_height_mm=1.5, num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=None,
)
sens_yes = run_dynamic_sensitivity(
    bead_height_mm=1.5, num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)

check(sens_yes["baseline_pressure"] < sens_no["baseline_pressure"],
      f"Sensitivity baseline: valve={sens_yes['baseline_pressure']:.4f} < no-valve={sens_no['baseline_pressure']:.4f}")

# Delta pattern should be same (valve is a constant offset, not position-dependent)
delta_no = sens_no["deltas"]
delta_yes = sens_yes["deltas"]
check(len(delta_no) == len(delta_yes), f"Same number of deltas: {len(delta_no)}")

max_delta_diff = max(abs(delta_no[i] - delta_yes[i]) for i in range(len(delta_no)))
check(max_delta_diff < 1e-6,
      f"Delta pattern identical (max diff = {max_delta_diff:.8f})")

# Critical point should be same
check(sens_no["critical_point"] == sens_yes["critical_point"],
      f"Same critical point: {sens_no['critical_point']} == {sens_yes['critical_point']}")


# ══════════════════════════════════════════════
#  Test 10: Bernoulli MC propagation
# ══════════════════════════════════════════════
print("\n[10] Bernoulli MC - valve parameter propagation")
from simulation import run_bernoulli_monte_carlo

bern_no = run_bernoulli_monte_carlo(
    p_bead=0.5, n_iterations=30, bead_height_mm=1.5,
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=None,
)
bern_yes = run_bernoulli_monte_carlo(
    p_bead=0.5, n_iterations=30, bead_height_mm=1.5,
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)

check(bern_yes["mean_pressure"] < bern_no["mean_pressure"],
      f"Bernoulli MC mean: valve={bern_yes['mean_pressure']:.4f} < no-valve={bern_no['mean_pressure']:.4f}")


# ══════════════════════════════════════════════
#  Test 11: Variable sweep propagation
# ══════════════════════════════════════════════
print("\n[11] Variable sweep - valve parameter propagation")
from simulation import run_variable_sweep

sweep_no = run_variable_sweep(
    sweep_variable="design_flow", start_val=200, end_val=600, step_val=200,
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, bead_height_mm=1.5,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=None,
)
sweep_yes = run_variable_sweep(
    sweep_variable="design_flow", start_val=200, end_val=600, step_val=200,
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, bead_height_mm=1.5,
    beads_per_branch=5, topology="tree",
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)

for i, Q in enumerate(sweep_no["sweep_values"]):
    check(sweep_yes["terminal_A"][i] < sweep_no["terminal_A"][i],
          f"Sweep Q={Q}: Case A with valve < without")
    check(sweep_yes["terminal_B"][i] < sweep_no["terminal_B"][i],
          f"Sweep Q={Q}: Case B with valve < without")


# ══════════════════════════════════════════════
#  Test 12: Manual formula verification at Q=1200 LPM
# ══════════════════════════════════════════════
print("\n[12] Manual formula verification at Q=1200 LPM")

Q_verify = 1200.0
D_verify = get_inner_diameter_m("100A")  # 0.10226 m
A_verify = math.pi * (D_verify / 2) ** 2
V_verify = (Q_verify / 60000.0) / A_verify

# h = K_total * V^2 / (2*g)
K_total = total_K  # 6.20
h_verify = K_total * V_verify**2 / (2 * G)
p_verify_mpa = RHO * G * h_verify / 1e6
p_verify_kpa = p_verify_mpa * 1000

print(f"  -- Q = {Q_verify} LPM, D = {D_verify*1000:.2f} mm")
print(f"  -- V = {V_verify:.4f} m/s")
print(f"  -- K_total = {K_total}")
print(f"  -- h_loss = {h_verify:.4f} m")
print(f"  -- P_loss = {p_verify_kpa:.2f} kPa")

# Compare with code
sys_verify = generate_dynamic_system(
    num_branches=4, heads_per_branch=8,
    inlet_pressure_mpa=1.4, total_flow_lpm=Q_verify,
)
res_verify = calculate_dynamic_system(
    sys_verify,
    equipment_k_factors=DEFAULT_EQUIPMENT_K_FACTORS,
    supply_pipe_size="100A",
)
code_loss_kpa = res_verify["equipment_loss_mpa"] * 1000

check(abs(code_loss_kpa - p_verify_kpa) < 0.01,
      f"Manual {p_verify_kpa:.2f} kPa == Code {code_loss_kpa:.2f} kPa")

# Check known value from previous analysis (~18.35 kPa)
check(15 < p_verify_kpa < 22, f"Q=1200 100A loss = {p_verify_kpa:.2f} kPa (expected ~18 kPa)")


# ══════════════════════════════════════════════
#  Summary
# ══════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"  TOTAL: {PASS + FAIL} tests | PASS: {PASS} | FAIL: {FAIL}")
if FAIL > 0:
    print(f"  ** {FAIL} test(s) FAILED **")
    sys.exit(1)
else:
    print("  ALL TESTS PASSED")
    sys.exit(0)
