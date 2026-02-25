# Integration test for Full Grid (Hardy-Cross) feature - ASCII only
import sys
import os
import time
import numpy as np

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


# == Test 1: Grid network generation ==
print("\n[1] Grid network generation")
from hardy_cross import generate_grid_network, solve_hardy_cross, calculate_grid_pressures, run_grid_system

net = generate_grid_network(
    num_branches=4, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
)

# Nodes: 2 rows x (4+1) cols = 10
check(len(net.nodes) == 10, f"10 nodes created (got {len(net.nodes)})")

# Pipes: 4(top) + 4(bot) + 4(branch) + 2(connectors) = 14
check(len(net.pipes) == 14, f"14 pipes created (got {len(net.pipes)})")

# Loops: 4 (n_branches for Full Grid with connectors)
check(len(net.loops) == 4, f"4 loops created (got {len(net.loops)})")
# Verify loops are not empty
check(all(len(l.pipe_ids) == 4 for l in net.loops), "All loops have 4 pipes")

# Check pipe types
cm_top = [p for p in net.pipes if p.pipe_type == "cm_top"]
cm_bot = [p for p in net.pipes if p.pipe_type == "cm_bot"]
branches = [p for p in net.pipes if p.pipe_type == "branch"]
connectors = [p for p in net.pipes if p.pipe_type == "connector"]
check(len(cm_top) == 4, f"4 cm_top pipes (got {len(cm_top)})")
check(len(cm_bot) == 4, f"4 cm_bot pipes (got {len(cm_bot)})")
check(len(branches) == 4, f"4 branch pipes (got {len(branches)})")
check(len(connectors) == 2, f"2 connector pipes (got {len(connectors)})")

# Branch has junctions
check(all(len(b.junctions) == 4 for b in branches), "Each branch has 4 junctions")


# == Test 2: Hardy-Cross solver convergence ==
print("\n[2] Hardy-Cross solver convergence")
hc = solve_hardy_cross(net)
check(hc["converged"], f"Converged in {hc['iterations']} iterations")
check(hc["iterations"] <= 100, f"Within max iterations: {hc['iterations']} <= 100")
check(hc["max_imbalance_m"] < 0.001, f"Imbalance {hc['max_imbalance_m']:.6f}m < 0.001m")
print(f"  HC result: {hc['iterations']} iterations, imbalance={hc['max_imbalance_m']:.6f}m")


# == Test 3: Grid pressure calculation ==
print("\n[3] Grid pressure calculation")
result = calculate_grid_pressures(net, hc_result=hc)

check("worst_terminal_mpa" in result, "Result has worst_terminal_mpa")
check("branch_profiles" in result, "Result has branch_profiles")
check("all_terminal_pressures" in result, "Result has all_terminal_pressures")
check(len(result["branch_profiles"]) == 4, "4 branch profiles")
check(len(result["all_terminal_pressures"]) == 4, "4 terminal pressures")
check(result["worst_terminal_mpa"] > 0, f"Positive terminal pressure: {result['worst_terminal_mpa']:.4f}")
check(result["topology"] == "grid", "Topology marked as grid")
check(result["hc_iterations"] == hc["iterations"], "HC iterations stored in result")

# Branch profiles have segment details
profile0 = result["branch_profiles"][0]
check(len(profile0["segment_details"]) == 4, "Branch 0 has 4 segment details")
check("weld_beads_in_seg" in profile0["segment_details"][0], "Segment details have weld_beads_in_seg")


# == Test 4: Grid vs Tree comparison - Grid should have higher pressure ==
print("\n[4] Grid vs Tree comparison (physical validity)")
from pipe_network import compare_dynamic_cases, compare_dynamic_cases_with_topology

common_params = dict(
    num_branches=4, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
    bead_height_existing=1.5, bead_height_new=0.0,
    beads_per_branch=0,
)

tree_result = compare_dynamic_cases_with_topology(topology="tree", **common_params)
grid_result = compare_dynamic_cases_with_topology(topology="grid", **common_params)

tree_p = tree_result["terminal_A_mpa"]
grid_p = grid_result["terminal_A_mpa"]

print(f"  Tree Case A terminal: {tree_p:.6f} MPa")
print(f"  Grid Case A terminal: {grid_p:.6f} MPa")

# Grid provides dual supply path -> lower losses -> higher terminal pressure
check(grid_p > tree_p, f"Grid pressure ({grid_p:.4f}) > Tree pressure ({tree_p:.4f})")

# Both should be positive
check(tree_p > 0, f"Tree terminal > 0")
check(grid_p > 0, f"Grid terminal > 0")

# Improvement percentages
check(grid_result["improvement_pct"] > 0, f"Grid improvement: {grid_result['improvement_pct']:.2f}%")
check(tree_result["improvement_pct"] > 0, f"Tree improvement: {tree_result['improvement_pct']:.2f}%")


# == Test 5: run_grid_system one-step function ==
print("\n[5] run_grid_system one-step function")
result_one = run_grid_system(
    num_branches=4, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
)
check(result_one["topology"] == "grid", "run_grid_system returns grid topology")
check(result_one["hc_converged"] is True, "run_grid_system converged")
check(result_one["worst_terminal_mpa"] > 0, f"One-step result positive: {result_one['worst_terminal_mpa']:.4f}")


# == Test 6: Grid with weld beads ==
print("\n[6] Grid with weld beads")
result_no_beads = run_grid_system(
    num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=0,
)
result_with_beads = run_grid_system(
    num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=5, bead_height_for_weld_mm=1.5,
)
check(
    result_with_beads["worst_terminal_mpa"] < result_no_beads["worst_terminal_mpa"],
    f"Weld beads reduce pressure: {result_with_beads['worst_terminal_mpa']:.4f} < {result_no_beads['worst_terminal_mpa']:.4f}",
)


# == Test 7: Monte Carlo with grid topology ==
print("\n[7] Monte Carlo with grid topology")
from simulation import run_dynamic_monte_carlo

mc = run_dynamic_monte_carlo(
    n_iterations=10, min_defects=1, max_defects=2,
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=0, topology="grid",
)
check(mc["mean_pressure"] > 0, f"MC grid mean > 0: {mc['mean_pressure']:.4f}")
check(mc["std_pressure"] >= 0, f"MC grid std >= 0: {mc['std_pressure']:.6f}")
check(len(mc["terminal_pressures"]) == 10, "MC 10 iterations recorded")


# == Test 8: Sensitivity with grid topology ==
print("\n[8] Sensitivity with grid topology")
from simulation import run_dynamic_sensitivity

sens = run_dynamic_sensitivity(
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=0, topology="grid",
)
check(len(sens["deltas"]) == 4, f"Sensitivity: 4 deltas (got {len(sens['deltas'])})")
check(sens["baseline_pressure"] > 0, f"Baseline > 0: {sens['baseline_pressure']:.4f}")
check(sens["critical_point"] in range(4), f"Critical point valid: {sens['critical_point']}")


# == Test 9: Pump P-Q with grid topology ==
print("\n[9] Pump P-Q with grid topology")
from pump import DynamicSystemCurve, load_pump, find_operating_point

pump = load_pump("Model A - Wilo Helix-V")

# * 대규모 시스템(20x10)으로 펌프 곡선과 교차점 검출 가능하도록 설정
ds_grid = DynamicSystemCurve(
    num_branches=20, heads_per_branch=10,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    bead_heights_2d=[[1.5]*10]*20,
    beads_per_branch=0,
    topology="grid",
)
h_grid = ds_grid.head_at_flow(400)
check(h_grid > 0, f"Grid system head at 400LPM: {h_grid:.2f}m")

op_grid = find_operating_point(pump, ds_grid)
if op_grid:
    check(True, f"Grid operating point: {op_grid['flow_lpm']:.0f}LPM @ {op_grid['head_m']:.1f}m")
else:
    # * 소규모 Grid는 시스템 저항이 매우 낮아 대형 펌프와 교차하지 않을 수 있음
    # * 시스템 곡선이 항상 펌프 곡선 아래에 있다면 "펌프 과잉"을 의미함
    h_at_max = ds_grid.head_at_flow(pump.max_flow)
    pump_at_max = pump.head_at_flow(pump.max_flow)
    check(h_at_max < pump_at_max, f"No intersection: system head ({h_at_max:.1f}m) < pump head ({pump_at_max:.1f}m) → pump oversized")


# == Test 10: Large scale convergence and performance ==
print("\n[10] Large scale: 50 branches x 10 heads")
t0 = time.time()
result_big = run_grid_system(
    num_branches=50, heads_per_branch=10,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=2000.0,
    beads_per_branch=5, bead_height_for_weld_mm=1.5,
)
dt = time.time() - t0
check(result_big["hc_converged"] is True, f"Large scale converged in {result_big['hc_iterations']} iterations")
check(dt < 30.0, f"Large scale time: {dt:.2f}s (<30s)")
check(result_big["worst_terminal_mpa"] > 0, f"Large scale terminal > 0: {result_big['worst_terminal_mpa']:.4f}")
check(result_big["total_heads"] == 500, f"500 total heads (got {result_big['total_heads']})")
print(f"  50x10 grid: {dt:.2f}s, {result_big['hc_iterations']} iterations, terminal={result_big['worst_terminal_mpa']:.4f} MPa")


# == Test 11: Loop energy balance verification ==
print("\n[11] Loop energy balance verification (Hardy-Cross guarantee)")
from hardy_cross import _pipe_head_loss
from constants import K1_BASE, K3
net2 = generate_grid_network(
    num_branches=3, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=300.0,
)
hc2 = solve_hardy_cross(net2)

# Hardy-Cross guarantees: sum of head losses around each loop ≈ 0
max_loop_error = 0.0
for loop in net2.loops:
    sum_hf = 0.0
    for pid, direction in zip(loop.pipe_ids, loop.directions):
        pipe = net2.pipes[pid]
        Q_signed = pipe.flow_lpm * direction
        Q_abs = abs(pipe.flow_lpm)
        h = _pipe_head_loss(pipe, Q_abs, K1_BASE, K3)
        if Q_signed >= 0:
            sum_hf += h
        else:
            sum_hf -= h
    max_loop_error = max(max_loop_error, abs(sum_hf))

check(
    max_loop_error < 0.01,
    f"Loop energy balance error < 0.01m: {max_loop_error:.6f}m",
)
check(hc2["converged"], f"Converged: {hc2['iterations']} iterations, imbalance={hc2['max_imbalance_m']:.6f}m")

# Verify all branch flows are positive (flowing TOP → BOT)
branch_flows = [p.flow_lpm for p in net2.pipes if p.pipe_type == "branch"]
check(
    all(f > 0 for f in branch_flows),
    f"All branch flows positive (TOP→BOT): {[round(f,2) for f in branch_flows]}",
)


# == Summary ==
print(f"\n{'='*50}")
print(f"RESULT: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
