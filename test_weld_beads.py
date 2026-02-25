# Integration test for weld bead feature - ASCII only
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


# ── Test 1: WeldBead dataclass & generate_branch_beads ──
print("\n[1] WeldBead + generate_branch_beads")
from pipe_network import WeldBead, generate_branch_beads
from constants import DEFAULT_BEADS_PER_BRANCH, MAX_BEADS_PER_BRANCH

check(DEFAULT_BEADS_PER_BRANCH == 5, "DEFAULT_BEADS_PER_BRANCH=5")
check(MAX_BEADS_PER_BRANCH == 20, "MAX_BEADS_PER_BRANCH=20")

# Uniform placement (rng=None)
pipe_sizes = ["50A", "50A", "50A", "40A", "40A", "32A", "25A", "25A"]
beads_uniform = generate_branch_beads(
    heads_per_branch=8, head_spacing_m=2.3, num_beads=5,
    bead_height_mm=1.5, pipe_sizes=pipe_sizes, rng=None,
)
check(len(beads_uniform) == 5, "Uniform: 5 beads generated")
check(all(isinstance(b, WeldBead) for b in beads_uniform), "Uniform: all are WeldBead")
check(all(b.bead_height_mm == 1.5 for b in beads_uniform), "Uniform: bead height 1.5mm")
check(all(b.K_value > 0 for b in beads_uniform), "Uniform: all K > 0")
check(all(0 <= b.segment_index < 8 for b in beads_uniform), "Uniform: valid segment indices")

# Verify uniform spacing
total_len = 8 * 2.3
step = total_len / 5
expected_positions = [step * (i + 0.5) for i in range(5)]
for i, b in enumerate(beads_uniform):
    actual_pos = b.segment_index * 2.3 + b.position_in_segment_m
    check(abs(actual_pos - expected_positions[i]) < 0.01,
          f"Uniform: bead {i} at {actual_pos:.2f}m (expected {expected_positions[i]:.2f}m)")

# Random placement
rng = np.random.default_rng(42)
beads_random = generate_branch_beads(
    heads_per_branch=8, head_spacing_m=2.3, num_beads=5,
    bead_height_mm=1.5, pipe_sizes=pipe_sizes, rng=rng,
)
check(len(beads_random) == 5, "Random: 5 beads generated")

# Two random runs should differ
rng2 = np.random.default_rng(99)
beads_random2 = generate_branch_beads(
    heads_per_branch=8, head_spacing_m=2.3, num_beads=5,
    bead_height_mm=1.5, pipe_sizes=pipe_sizes, rng=rng2,
)
positions_r1 = [(b.segment_index, b.position_in_segment_m) for b in beads_random]
positions_r2 = [(b.segment_index, b.position_in_segment_m) for b in beads_random2]
check(positions_r1 != positions_r2, "Random: different seeds produce different positions")

# Zero beads
beads_zero = generate_branch_beads(
    heads_per_branch=8, head_spacing_m=2.3, num_beads=0,
    bead_height_mm=1.5, pipe_sizes=pipe_sizes,
)
check(len(beads_zero) == 0, "Zero beads: empty list")


# ── Test 2: Pressure calculation with weld beads ──
print("\n[2] Pressure calc with weld beads")
from pipe_network import generate_dynamic_system, calculate_dynamic_system

# Without weld beads
sys_no_beads = generate_dynamic_system(
    num_branches=2, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    bead_heights_2d=[[1.5]*4]*2,
    beads_per_branch=0,
)
res_no_beads = calculate_dynamic_system(sys_no_beads)

# With weld beads (uniform)
sys_with_beads = generate_dynamic_system(
    num_branches=2, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    bead_heights_2d=[[1.5]*4]*2,
    beads_per_branch=5,
    bead_height_for_weld_mm=1.5,
    rng=None,
)
res_with_beads = calculate_dynamic_system(sys_with_beads)

check(sys_with_beads.beads_per_branch == 5, "System stores beads_per_branch=5")
check(len(sys_with_beads.branches[0].weld_beads) == 5, "Branch 0 has 5 weld beads")
check(len(sys_with_beads.branches[1].weld_beads) == 5, "Branch 1 has 5 weld beads")

# Weld beads should increase losses -> lower terminal pressure
p_no = res_no_beads["worst_terminal_mpa"]
p_with = res_with_beads["worst_terminal_mpa"]
check(p_with < p_no, f"Weld beads lower pressure: {p_with:.4f} < {p_no:.4f} MPa")
print(f"  Without weld beads: {p_no:.6f} MPa")
print(f"  With 5 weld beads:  {p_with:.6f} MPa")
print(f"  Difference:         {(p_no - p_with)*1000:.4f} kPa")

# Segment details should have weld_beads_in_seg field
profile = res_with_beads["branch_profiles"][0]
det = profile["segment_details"]
check("weld_beads_in_seg" in det[0], "segment_details has weld_beads_in_seg")
check("weld_bead_loss_mpa" in det[0], "segment_details has weld_bead_loss_mpa")
total_seg_beads = sum(d["weld_beads_in_seg"] for d in det)
check(total_seg_beads == 5, f"Total weld beads across segments = {total_seg_beads}")


# ── Test 3: Case A vs B with weld beads ──
print("\n[3] compare_dynamic_cases with weld beads")
from pipe_network import compare_dynamic_cases

# Without weld beads
case_no = compare_dynamic_cases(
    num_branches=2, heads_per_branch=4,
    bead_height_existing=1.5, bead_height_new=0.0,
    beads_per_branch=0,
)
# With weld beads
case_with = compare_dynamic_cases(
    num_branches=2, heads_per_branch=4,
    bead_height_existing=1.5, bead_height_new=0.0,
    beads_per_branch=5,
)

check(case_with["terminal_A_mpa"] < case_no["terminal_A_mpa"],
      "Case A with weld beads < without")
check(abs(case_with["terminal_B_mpa"] - case_no["terminal_B_mpa"]) < 1e-6,
      "Case B unchanged (no weld beads for new tech)")
check(case_with["improvement_pct"] > case_no["improvement_pct"],
      "Improvement % higher with weld beads (larger gap)")
print(f"  Without weld beads: improvement = {case_no['improvement_pct']:.4f}%")
print(f"  With weld beads:    improvement = {case_with['improvement_pct']:.4f}%")


# ── Test 4: Monte Carlo with weld bead re-randomization ──
print("\n[4] Monte Carlo with weld bead position variance")

from simulation import run_dynamic_monte_carlo

# MC without weld beads
mc_no = run_dynamic_monte_carlo(
    n_iterations=30, min_defects=1, max_defects=2,
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=0,
)
# MC with weld beads (positions randomized each iteration)
mc_with = run_dynamic_monte_carlo(
    n_iterations=30, min_defects=1, max_defects=2,
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=5,
)

check(mc_with["beads_per_branch"] == 5, "MC output includes beads_per_branch=5")
check(mc_with["mean_pressure"] < mc_no["mean_pressure"],
      f"MC mean lower with beads: {mc_with['mean_pressure']:.4f} < {mc_no['mean_pressure']:.4f}")
check(mc_with["std_pressure"] > 0, f"MC std > 0 (variance exists): {mc_with['std_pressure']:.6f}")

# Key test: bead positions create additional variance
# With beads, even same defect count should have more spread from position randomness
print(f"  MC without beads: mean={mc_no['mean_pressure']:.4f}, std={mc_no['std_pressure']:.6f}")
print(f"  MC with beads:    mean={mc_with['mean_pressure']:.4f}, std={mc_with['std_pressure']:.6f}")


# ── Test 5: Pump P-Q with weld beads ──
print("\n[5] DynamicSystemCurve with weld beads")
from pump import DynamicSystemCurve, load_pump, find_operating_point

pump = load_pump("Model A - Wilo Helix-V")

dsA_no = DynamicSystemCurve(
    num_branches=2, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    bead_heights_2d=[[1.5]*4]*2,
    beads_per_branch=0,
)
dsA_with = DynamicSystemCurve(
    num_branches=2, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    bead_heights_2d=[[1.5]*4]*2,
    beads_per_branch=5,
    bead_height_for_weld_mm=1.5,
)

h_no = dsA_no.head_at_flow(400)
h_with = dsA_with.head_at_flow(400)
check(h_with > h_no, f"System curve higher with beads: {h_with:.2f} > {h_no:.2f} m")

opA_no = find_operating_point(pump, dsA_no)
opA_with = find_operating_point(pump, dsA_with)
if opA_no and opA_with:
    check(True, f"Operating points found: no_beads={opA_no['flow_lpm']:.0f}LPM, with={opA_with['flow_lpm']:.0f}LPM")
    check(opA_with["flow_lpm"] <= opA_no["flow_lpm"],
          "Higher resistance -> lower or equal operating flow")
else:
    check(False, "Operating point not found")


# ── Test 6: Sensitivity with weld beads ──
print("\n[6] Sensitivity analysis with weld beads")
from simulation import run_dynamic_sensitivity

sens = run_dynamic_sensitivity(
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
    beads_per_branch=5,
)
check(len(sens["deltas"]) == 4, "Sensitivity: 4 deltas")
check(sens["critical_point"] in range(4), "Sensitivity: valid critical point")
# Baseline should reflect weld beads
check(sens["baseline_pressure"] < 1.4, "Baseline reflects weld bead losses")


# ── Test 7: Edge cases ──
print("\n[7] Edge cases")

# Max beads (20)
beads_max = generate_branch_beads(
    heads_per_branch=4, head_spacing_m=2.3, num_beads=20,
    bead_height_mm=1.5, pipe_sizes=["40A","40A","32A","25A"], rng=np.random.default_rng(),
)
check(len(beads_max) == 20, "20 beads generated (max)")

# Single head branch with beads
sys_single = generate_dynamic_system(
    num_branches=1, heads_per_branch=1,
    beads_per_branch=3,
    bead_height_for_weld_mm=1.5,
    inlet_pressure_mpa=1.4, total_flow_lpm=100.0,
)
res_single = calculate_dynamic_system(sys_single)
check(res_single["worst_terminal_mpa"] > 0, "Single head + 3 beads: positive pressure")

# Large scale with beads
t0 = time.time()
sys_big = generate_dynamic_system(
    num_branches=50, heads_per_branch=10,
    beads_per_branch=10,
    bead_height_for_weld_mm=1.5,
    inlet_pressure_mpa=1.4, total_flow_lpm=2000.0,
)
res_big = calculate_dynamic_system(sys_big)
dt = time.time() - t0
total_beads = sum(len(b.weld_beads) for b in sys_big.branches)
check(total_beads == 500, f"50 branches x 10 beads = {total_beads} total weld beads")
check(dt < 3.0, f"Large scale with beads: {dt:.2f}s (<3s)")


# ── Summary ──
print(f"\n{'='*50}")
print(f"RESULT: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
