# ! 소화배관 시뮬레이션 — 몬테카를로, 민감도 분석, 임계점 탐색
# * 동적 배관망(n 가지배관 × m 헤드) 전체에 대한 통계 분석

import numpy as np
from typing import List, Optional

from constants import (
    NUM_HEADS, DEFAULT_MC_ITERATIONS,
    DEFAULT_MIN_DEFECTS, DEFAULT_MAX_DEFECTS,
    DEFAULT_INLET_PRESSURE_MPA, DEFAULT_TOTAL_FLOW_LPM,
    DEFAULT_FITTING_SPACING_M, K1_BASE, K2, K3,
    MIN_TERMINAL_PRESSURE_MPA,
    DEFAULT_NUM_BRANCHES, DEFAULT_HEADS_PER_BRANCH,
    DEFAULT_BRANCH_SPACING_M, DEFAULT_HEAD_SPACING_M,
    DEFAULT_BEADS_PER_BRANCH,
)
from pipe_network import (
    generate_dynamic_system, calculate_dynamic_system,
    build_default_network, calculate_pressure_profile,
    compare_dynamic_cases_with_topology,
)


# ══════════════════════════════════════════════
#  동적 시스템 몬테카를로 시뮬레이션
# ══════════════════════════════════════════════

def run_dynamic_monte_carlo(
    n_iterations: int = DEFAULT_MC_ITERATIONS,
    min_defects: int = DEFAULT_MIN_DEFECTS,
    max_defects: int = DEFAULT_MAX_DEFECTS,
    bead_height_mm: float = 1.5,
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
    beads_per_branch: int = 0,
    topology: str = "tree",
    relaxation: float = 0.5,
) -> dict:
    """
    ! 동적 시스템 몬테카를로: 이음쇠 결함 + 용접 비드 위치 무작위 시뮬레이션

    * 이음쇠 결함: n×m개 중 무작위 1~3개 배치 (기존)
    * 용접 비드 위치: 매 반복마다 beads_per_branch개 비드가 각 가지배관의
      직관 구간 내 새로운 임의 좌표에 재배치 (신규)
    * 동일한 비드 개수라도 위치 변화에 따른 말단 압력 산포도 계산

    반환:
        worst_terminal_pressures : 각 반복의 최악 말단 압력
        defect_configs           : 각 반복의 이음쇠 결함 위치 [(b,h), ...]
        mean/std/min/max         : 통계값
        p_below_threshold        : 0.1 MPa 미달 확률
        defect_frequency_2d      : (n_branches × heads_per_branch) 이음쇠 결함 빈도 배열
        beads_per_branch         : 가지배관당 용접 비드 개수 (참조용)
    """
    rng = np.random.default_rng()
    total_fittings = num_branches * heads_per_branch

    # * max_defects가 전체 이음쇠 수를 초과하지 않도록 클램프
    effective_max = min(max_defects, total_fittings)
    effective_min = min(min_defects, effective_max)

    worst_pressures = np.zeros(n_iterations)
    defect_configs = []
    defect_frequency = np.zeros((num_branches, heads_per_branch))

    common = dict(
        num_branches=num_branches,
        heads_per_branch=heads_per_branch,
        branch_spacing_m=branch_spacing_m,
        head_spacing_m=head_spacing_m,
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        K1_base=K1_base,
        K2_val=K2_val,
    )

    for trial in range(n_iterations):
        num_defects = rng.integers(effective_min, effective_max + 1)

        # * 전체 이음쇠를 1D 인덱스로 매핑 → 무작위 선택
        flat_positions = rng.choice(total_fittings, size=num_defects, replace=False)

        # * 2D 이음쇠 비드 배열 구성
        beads_2d = [[0.0] * heads_per_branch for _ in range(num_branches)]
        positions_2d = []
        for flat_idx in sorted(flat_positions.tolist()):
            b = flat_idx // heads_per_branch
            h = flat_idx % heads_per_branch
            beads_2d[b][h] = bead_height_mm
            defect_frequency[b][h] += 1
            positions_2d.append((b, h))

        # * 시스템 빌드 — 용접 비드는 rng로 매 반복 새로운 위치에 재배치
        if topology == "grid":
            from hardy_cross import run_grid_system
            result = run_grid_system(
                bead_heights_2d=beads_2d,
                beads_per_branch=beads_per_branch,
                bead_height_for_weld_mm=bead_height_mm,
                rng=rng,
                K3_val=K3_val,
                relaxation=relaxation,
                **common,
            )
        else:
            system = generate_dynamic_system(
                bead_heights_2d=beads_2d,
                beads_per_branch=beads_per_branch,
                bead_height_for_weld_mm=bead_height_mm,
                rng=rng,
                **common,
            )
            result = calculate_dynamic_system(system, K3_val)

        worst_pressures[trial] = result["worst_terminal_mpa"]
        defect_configs.append(positions_2d)

    below_threshold = np.sum(worst_pressures < MIN_TERMINAL_PRESSURE_MPA)

    return {
        "terminal_pressures": worst_pressures,
        "defect_configs": defect_configs,
        "mean_pressure": float(np.mean(worst_pressures)),
        "std_pressure": float(np.std(worst_pressures)),
        "min_pressure": float(np.min(worst_pressures)),
        "max_pressure": float(np.max(worst_pressures)),
        "p_below_threshold": float(below_threshold / n_iterations),
        "defect_frequency_2d": defect_frequency,
        # * UI 호환용 1D 집계 (가지배관별 총 빈도)
        "defect_frequency": defect_frequency.sum(axis=1),
        "n_iterations": n_iterations,
        "total_fittings": total_fittings,
        "beads_per_branch": beads_per_branch,
    }


# ══════════════════════════════════════════════
#  동적 시스템 민감도 분석
# ══════════════════════════════════════════════

def run_dynamic_sensitivity(
    bead_height_mm: float = 1.5,
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
    beads_per_branch: int = 0,
    topology: str = "tree",
    relaxation: float = 0.5,
) -> dict:
    """
    ! 동적 시스템 민감도 분석

    * 최악 가지배관(가장 원격)의 각 헤드 위치에 단독 이음쇠 비드 배치
    * 직관 용접 비드(beads_per_branch)는 균등 배치로 기준선에 포함
    * 어느 헤드 위치가 가장 치명적인지 식별

    반환:
        baseline_pressure      : 비드 없을 때 최악 말단 압력
        single_bead_pressures  : 각 위치별 말단 압력
        deltas                 : 각 위치별 압력 강하량
        ranking                : 영향도 순위
        critical_point         : 가장 치명적 위치 (헤드 인덱스)
        worst_branch           : 분석 대상 가지배관 인덱스
        pipe_sizes             : 각 위치의 관경
    """
    common = dict(
        num_branches=num_branches,
        heads_per_branch=heads_per_branch,
        branch_spacing_m=branch_spacing_m,
        head_spacing_m=head_spacing_m,
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        K1_base=K1_base,
        K2_val=K2_val,
    )

    # * 기준선: 이음쇠 비드 없음, 직관 용접 비드 포함 (균등 배치)
    if topology == "grid":
        from hardy_cross import run_grid_system
        res_base = run_grid_system(
            beads_per_branch=beads_per_branch,
            bead_height_for_weld_mm=bead_height_mm,
            K3_val=K3_val,
            relaxation=relaxation,
            **common,
        )
    else:
        sys_base = generate_dynamic_system(
            beads_per_branch=beads_per_branch,
            bead_height_for_weld_mm=bead_height_mm,
            **common,
        )
        res_base = calculate_dynamic_system(sys_base, K3_val)
    baseline = res_base["worst_terminal_mpa"]
    worst_branch = res_base["worst_branch_index"]

    # * 최악 가지배관의 관경 추출 (Tree 에서는 branches에서, Grid에서는 프로파일에서)
    if topology == "grid":
        pipe_sizes = [d["pipe_size"] for d in res_base["branch_profiles"][worst_branch]["segment_details"]]
    else:
        pipe_sizes = sys_base.branches[worst_branch].pipe_sizes

    # * 최악 가지배관의 각 헤드 위치에 단독 이음쇠 비드
    single_pressures = []
    deltas = []

    for h in range(heads_per_branch):
        beads_2d = [[0.0] * heads_per_branch for _ in range(num_branches)]
        beads_2d[worst_branch][h] = bead_height_mm

        if topology == "grid":
            result = run_grid_system(
                bead_heights_2d=beads_2d,
                beads_per_branch=beads_per_branch,
                bead_height_for_weld_mm=bead_height_mm,
                K3_val=K3_val,
                relaxation=relaxation,
                **common,
            )
        else:
            system = generate_dynamic_system(
                bead_heights_2d=beads_2d,
                beads_per_branch=beads_per_branch,
                bead_height_for_weld_mm=bead_height_mm,
                **common,
            )
            result = calculate_dynamic_system(system, K3_val)
        p = result["worst_terminal_mpa"]

        single_pressures.append(p)
        deltas.append(baseline - p)

    ranking = sorted(range(heads_per_branch), key=lambda x: deltas[x], reverse=True)

    return {
        "baseline_pressure": baseline,
        "single_bead_pressures": single_pressures,
        "deltas": deltas,
        "ranking": ranking,
        "critical_point": ranking[0],
        "worst_branch": worst_branch,
        "pipe_sizes": pipe_sizes,
        "bead_height_mm": bead_height_mm,
        "heads_per_branch": heads_per_branch,
    }


# ══════════════════════════════════════════════
#  연속 변수 스캐닝 (Variable Sweep)
# ══════════════════════════════════════════════

def run_variable_sweep(
    sweep_variable: str,
    start_val: float,
    end_val: float,
    step_val: float,
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    bead_height_mm: float = 1.5,
    beads_per_branch: int = DEFAULT_BEADS_PER_BRANCH,
    topology: str = "tree",
    relaxation: float = 0.5,
    mc_min_defects: int = DEFAULT_MIN_DEFECTS,
    mc_max_defects: int = DEFAULT_MAX_DEFECTS,
) -> dict:
    """
    특정 설계 변수를 연속 변화시키며 Case A/B 말단 수압 변화 및 임계점을 탐지.

    sweep_variable: "design_flow" | "inlet_pressure" | "bead_height"
                  | "heads_per_branch" | "mc_iterations"
    """
    sweep_values = np.arange(start_val, end_val + step_val / 2, step_val).tolist()
    if not sweep_values:
        sweep_values = [start_val]

    # ── 몬테카를로 반복 횟수 스캔: MC 통계값 수집 ──
    if sweep_variable == "mc_iterations":
        mc_mean = []
        mc_std = []
        mc_min = []
        mc_max = []
        mc_p_below = []

        for val in sweep_values:
            n_iter = max(1, int(val))
            try:
                mc_res = run_dynamic_monte_carlo(
                    n_iterations=n_iter,
                    min_defects=mc_min_defects,
                    max_defects=mc_max_defects,
                    bead_height_mm=bead_height_mm,
                    num_branches=num_branches,
                    heads_per_branch=heads_per_branch,
                    branch_spacing_m=branch_spacing_m,
                    head_spacing_m=head_spacing_m,
                    inlet_pressure_mpa=inlet_pressure_mpa,
                    total_flow_lpm=total_flow_lpm,
                    beads_per_branch=beads_per_branch,
                    topology=topology,
                    relaxation=relaxation,
                )
                mc_mean.append(mc_res["mean_pressure"])
                mc_std.append(mc_res["std_pressure"])
                mc_min.append(mc_res["min_pressure"])
                mc_max.append(mc_res["max_pressure"])
                mc_p_below.append(mc_res["p_below_threshold"])
            except Exception:
                mc_mean.append(0.0)
                mc_std.append(0.0)
                mc_min.append(0.0)
                mc_max.append(0.0)
                mc_p_below.append(0.0)

        return {
            "sweep_variable": sweep_variable,
            "sweep_values": sweep_values,
            "mc_mean": mc_mean,
            "mc_std": mc_std,
            "mc_min": mc_min,
            "mc_max": mc_max,
            "mc_p_below": mc_p_below,
        }

    # ── 기존 변수 스캔: Case A/B 비교 ──
    terminal_A = []
    terminal_B = []
    improvement_pct = []
    pass_fail_A = []
    pass_fail_B = []

    for val in sweep_values:
        kw = dict(
            topology=topology,
            num_branches=num_branches,
            heads_per_branch=heads_per_branch,
            branch_spacing_m=branch_spacing_m,
            head_spacing_m=head_spacing_m,
            inlet_pressure_mpa=inlet_pressure_mpa,
            total_flow_lpm=total_flow_lpm,
            bead_height_existing=bead_height_mm,
            bead_height_new=0.0,
            beads_per_branch=beads_per_branch,
            relaxation=relaxation,
        )

        if sweep_variable == "design_flow":
            kw["total_flow_lpm"] = float(val)
        elif sweep_variable == "inlet_pressure":
            kw["inlet_pressure_mpa"] = float(val)
        elif sweep_variable == "bead_height":
            kw["bead_height_existing"] = float(val)
        elif sweep_variable == "heads_per_branch":
            kw["heads_per_branch"] = int(val)

        try:
            res = compare_dynamic_cases_with_topology(**kw)
            t_a = res["terminal_A_mpa"]
            t_b = res["terminal_B_mpa"]
            imp = res["improvement_pct"]
        except Exception:
            t_a = 0.0
            t_b = 0.0
            imp = 0.0

        terminal_A.append(t_a)
        terminal_B.append(t_b)
        improvement_pct.append(imp)
        pass_fail_A.append(t_a >= MIN_TERMINAL_PRESSURE_MPA)
        pass_fail_B.append(t_b >= MIN_TERMINAL_PRESSURE_MPA)

    # 임계점 탐지: PASS → FAIL 최초 전환 값
    critical_A = None
    critical_B = None
    for i in range(len(sweep_values)):
        if critical_A is None and not pass_fail_A[i]:
            critical_A = sweep_values[i]
        if critical_B is None and not pass_fail_B[i]:
            critical_B = sweep_values[i]

    return {
        "sweep_variable": sweep_variable,
        "sweep_values": sweep_values,
        "terminal_A": terminal_A,
        "terminal_B": terminal_B,
        "improvement_pct": improvement_pct,
        "pass_fail_A": pass_fail_A,
        "pass_fail_B": pass_fail_B,
        "critical_A": critical_A,
        "critical_B": critical_B,
    }


# ══════════════════════════════════════════════
#  레거시 호환 함수 (하위 호환)
# ══════════════════════════════════════════════

def run_monte_carlo(
    n_iterations: int = DEFAULT_MC_ITERATIONS,
    min_defects: int = DEFAULT_MIN_DEFECTS,
    max_defects: int = DEFAULT_MAX_DEFECTS,
    bead_height_mm: float = 1.5,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    fitting_spacing_m: float = DEFAULT_FITTING_SPACING_M,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
) -> dict:
    """레거시: 고정 8헤드 몬테카를로 (하위 호환)"""
    rng = np.random.default_rng()
    terminal_pressures = np.zeros(n_iterations)
    defect_configs = []
    defect_frequency = np.zeros(NUM_HEADS)

    for trial in range(n_iterations):
        num_defects = rng.integers(min_defects, max_defects + 1)
        defect_positions = rng.choice(NUM_HEADS, size=num_defects, replace=False)
        defect_positions = sorted(defect_positions.tolist())

        bead_heights = [0.0] * NUM_HEADS
        for pos in defect_positions:
            bead_heights[pos] = bead_height_mm
            defect_frequency[pos] += 1

        network = build_default_network(
            inlet_pressure_mpa=inlet_pressure_mpa,
            total_flow_lpm=total_flow_lpm,
            fitting_spacing_m=fitting_spacing_m,
            bead_heights=bead_heights,
            K1_base=K1_base, K2_val=K2_val, K3_val=K3_val,
        )
        profile = calculate_pressure_profile(network)
        terminal_pressures[trial] = profile["terminal_pressure_mpa"]
        defect_configs.append(defect_positions)

    below_threshold = np.sum(terminal_pressures < MIN_TERMINAL_PRESSURE_MPA)
    return {
        "terminal_pressures": terminal_pressures,
        "defect_configs": defect_configs,
        "mean_pressure": float(np.mean(terminal_pressures)),
        "std_pressure": float(np.std(terminal_pressures)),
        "min_pressure": float(np.min(terminal_pressures)),
        "max_pressure": float(np.max(terminal_pressures)),
        "p_below_threshold": float(below_threshold / n_iterations),
        "defect_frequency": defect_frequency,
        "n_iterations": n_iterations,
    }


def run_sensitivity_analysis(
    bead_height_mm: float = 1.5,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    fitting_spacing_m: float = DEFAULT_FITTING_SPACING_M,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
) -> dict:
    """레거시: 고정 8헤드 민감도 분석 (하위 호환)"""
    common_params = dict(
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        fitting_spacing_m=fitting_spacing_m,
        K1_base=K1_base, K2_val=K2_val, K3_val=K3_val,
    )

    network_base = build_default_network(bead_heights=[0.0] * NUM_HEADS, **common_params)
    profile_base = calculate_pressure_profile(network_base)
    baseline = profile_base["terminal_pressure_mpa"]

    single_pressures = []
    deltas = []

    for i in range(NUM_HEADS):
        beads = [0.0] * NUM_HEADS
        beads[i] = bead_height_mm
        network = build_default_network(bead_heights=beads, **common_params)
        profile = calculate_pressure_profile(network)
        p_i = profile["terminal_pressure_mpa"]
        single_pressures.append(p_i)
        deltas.append(baseline - p_i)

    ranking = sorted(range(NUM_HEADS), key=lambda x: deltas[x], reverse=True)

    return {
        "baseline_pressure": baseline,
        "single_bead_pressures": single_pressures,
        "deltas": deltas,
        "ranking": ranking,
        "critical_point": ranking[0],
        "bead_height_mm": bead_height_mm,
    }
