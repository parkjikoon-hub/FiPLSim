# Integration test - all print statements use ASCII only
import sys
import os
import time

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


# ── Test 1: constants.py ──
print("\n[1] constants.py")
from constants import (
    PIPE_DIMENSIONS, auto_pipe_size, auto_cross_main_size,
    get_inner_diameter_m, K1_BASE, K2, K3, K_TEE_RUN,
    MAX_BRANCHES, MAX_HEADS_PER_BRANCH,
)
check("80A" in PIPE_DIMENSIONS, "80A pipe exists")
check("100A" in PIPE_DIMENSIONS, "100A pipe exists")
check(auto_pipe_size(1) == "25A", "1 head -> 25A")
check(auto_pipe_size(8) == "50A", "8 heads -> 50A")
check(auto_pipe_size(15) == "65A", "15 heads -> 65A")
check(auto_cross_main_size(10) == "65A", "10 total -> 65A cross main")
check(auto_cross_main_size(25) == "80A", "25 total -> 80A cross main")
check(auto_cross_main_size(50) == "100A", "50 total -> 100A cross main")
check(MAX_BRANCHES == 200, "MAX_BRANCHES=200")
check(MAX_HEADS_PER_BRANCH == 50, "MAX_HEADS_PER_BRANCH=50")

# ── Test 2: pipe_network.py (dynamic system) ──
print("\n[2] pipe_network.py - dynamic system")
from pipe_network import (
    generate_dynamic_system, calculate_dynamic_system,
    compare_dynamic_cases, ValidationError,
    build_default_network, calculate_pressure_profile,
)

# 2a: Basic generation (4 branches x 8 heads)
sys_obj = generate_dynamic_system(
    num_branches=4, heads_per_branch=8,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    inlet_pressure_mpa=1.4, total_flow_lpm=400.0,
)
check(sys_obj.num_branches == 4, "4 branches created")
check(len(sys_obj.branches) == 4, "4 branch objects")
check(len(sys_obj.branches[0].junctions) == 8, "8 heads per branch")
check(sys_obj.cross_main_size == "80A", "32 heads -> 80A cross main")

# 2b: Pressure calculation
result = calculate_dynamic_system(sys_obj)
check("worst_branch_index" in result, "worst branch identified")
check("all_terminal_pressures" in result, "all terminal pressures returned")
check(len(result["all_terminal_pressures"]) == 4, "4 terminal pressures")
check(result["worst_terminal_mpa"] > 0, "positive terminal pressure")
# Worst branch should be the last one (furthest from inlet)
check(result["worst_branch_index"] == 3, "worst branch is B#4 (furthest)")

# 2c: Case comparison
case = compare_dynamic_cases(
    num_branches=4, heads_per_branch=8,
    bead_height_existing=1.5, bead_height_new=0.0,
)
check(case["terminal_B_mpa"] > case["terminal_A_mpa"],
      "Case B (new tech) > Case A (old tech)")
check(case["improvement_pct"] > 0, "positive improvement percentage")
check(case["pass_fail_B"], "Case B passes 0.1 MPa threshold")
print(f"  Terminal A: {case['terminal_A_mpa']:.4f} MPa")
print(f"  Terminal B: {case['terminal_B_mpa']:.4f} MPa")
print(f"  Improvement: {case['improvement_pct']:.2f}%")

# 2d: Validation errors
print("\n[3] Input validation")
try:
    generate_dynamic_system(num_branches=0)
    check(False, "should reject num_branches=0")
except ValidationError:
    check(True, "rejects num_branches=0")

try:
    generate_dynamic_system(num_branches=-1)
    check(False, "should reject negative num_branches")
except ValidationError:
    check(True, "rejects negative num_branches")

try:
    generate_dynamic_system(num_branches=201)
    check(False, "should reject num_branches > 200")
except ValidationError:
    check(True, "rejects num_branches > 200")

try:
    generate_dynamic_system(heads_per_branch=51)
    check(False, "should reject heads_per_branch > 50")
except ValidationError:
    check(True, "rejects heads_per_branch > 50")

try:
    generate_dynamic_system(head_spacing_m=-1.0)
    check(False, "should reject negative spacing")
except ValidationError:
    check(True, "rejects negative spacing")

# ── Test 3: simulation.py ──
print("\n[4] simulation.py - MC & sensitivity")
from simulation import run_dynamic_monte_carlo, run_dynamic_sensitivity

mc = run_dynamic_monte_carlo(
    n_iterations=20, min_defects=1, max_defects=2,
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
)
check(len(mc["terminal_pressures"]) == 20, "MC: 20 iterations")
check(mc["total_fittings"] == 8, "MC: 2x4=8 fittings")
check(mc["mean_pressure"] > 0, "MC: positive mean pressure")
check(mc["defect_frequency_2d"].shape == (2, 4), "MC: 2D frequency shape (2,4)")

sens = run_dynamic_sensitivity(
    bead_height_mm=1.5, num_branches=2, heads_per_branch=4,
    inlet_pressure_mpa=1.4, total_flow_lpm=200.0,
)
check(len(sens["deltas"]) == 4, "Sensitivity: 4 delta values")
check(sens["critical_point"] in range(4), "Sensitivity: valid critical point")
check(all(d >= 0 for d in sens["deltas"]), "Sensitivity: all deltas >= 0")

# ── Test 4: pump.py ──
print("\n[5] pump.py - PQ curve & operating point")
from pump import (
    load_pump, DynamicSystemCurve, find_operating_point,
    calculate_energy_savings,
)

pump = load_pump("Model A - Wilo Helix-V")
check(pump.head_at_flow(1000) > 0, "Pump: positive head at 1000 LPM")

dsA = DynamicSystemCurve(
    num_branches=2, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    bead_heights_2d=[[1.5]*4]*2,
)
dsB = DynamicSystemCurve(
    num_branches=2, heads_per_branch=4,
    branch_spacing_m=3.5, head_spacing_m=2.3,
    bead_heights_2d=[[0.0]*4]*2,
)
check(dsA.head_at_flow(400) > 0, "Dynamic system curve A: positive head")
check(dsB.head_at_flow(400) > 0, "Dynamic system curve B: positive head")
check(dsA.head_at_flow(400) >= dsB.head_at_flow(400),
      "System A requires >= system B head")

opA = find_operating_point(pump, dsA)
opB = find_operating_point(pump, dsB)
if opA and opB:
    check(True, f"Operating points found: A={opA['flow_lpm']:.0f}LPM, B={opB['flow_lpm']:.0f}LPM")
    energy = calculate_energy_savings(opA, opB)
    check(energy["delta_power_kw"] >= 0, "Energy savings >= 0")
    print(f"  Power saving: {energy['delta_power_kw']:.3f} kW")
    print(f"  Annual saving: {energy['annual_cost_savings_krw']:,.0f} KRW")
else:
    check(False, "Operating points NOT found - pump may not intersect system")

# ── Test 5: Legacy compatibility ──
print("\n[6] Legacy compatibility (8-head)")
net = build_default_network()
prof = calculate_pressure_profile(net)
check(len(prof["pressures_mpa"]) == 9, "Legacy: 9 pressure points (inlet + 8 heads)")
check(prof["terminal_pressure_mpa"] > 0, "Legacy: positive terminal pressure")

# ── Test 6: Large scale ──
print("\n[7] Large scale (100 branches x 10 heads)")
t0 = time.time()
big_sys = generate_dynamic_system(
    num_branches=100, heads_per_branch=10,
    inlet_pressure_mpa=1.4, total_flow_lpm=2000.0,
)
big_result = calculate_dynamic_system(big_sys)
dt = time.time() - t0
check(big_result["total_heads"] == 1000, "1000 total heads")
check(big_result["cross_main_size"] == "100A", "100A cross main for 1000 heads")
check(dt < 5.0, f"Large scale completed in {dt:.2f}s (<5s)")
check(big_result["worst_terminal_mpa"] > 0, "Large scale: positive terminal pressure")

# ── Summary ──
print(f"\n{'='*50}")
print(f"RESULT: {PASS} passed, {FAIL} failed, {PASS+FAIL} total")
print(f"{'='*50}")
sys.exit(0 if FAIL == 0 else 1)
