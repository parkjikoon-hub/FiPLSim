# ! 소화배관 시뮬레이션 — 펌프 P-Q 곡선 보간, 운전점 계산, 에너지 절감 분석
# * scipy interp1d(cubic) + brentq 루트 파인딩
# * 동적 시스템 + 레거시 시스템 모두 지원

from typing import Tuple, Optional, List
import numpy as np
from scipy.interpolate import interp1d
from scipy.optimize import brentq

from constants import (
    PUMP_DATABASE, RHO, G, NUM_HEADS,
    DEFAULT_INLET_PRESSURE_MPA, DEFAULT_TOTAL_FLOW_LPM,
    DEFAULT_FITTING_SPACING_M, DEFAULT_OPERATING_HOURS_PER_YEAR,
    DEFAULT_ELECTRICITY_RATE_KRW, MIN_TERMINAL_PRESSURE_MPA,
    DEFAULT_NUM_BRANCHES, DEFAULT_HEADS_PER_BRANCH,
    DEFAULT_BRANCH_SPACING_M, DEFAULT_HEAD_SPACING_M,
    K1_BASE, K2, K3,
)
from hydraulics import mpa_to_head, head_to_mpa
from pipe_network import (
    build_default_network, calculate_pressure_profile,
    generate_dynamic_system, calculate_dynamic_system,
    generate_branch_beads,
)


# ──────────────────────────────────────────────
# ? 펌프 P-Q 곡선 클래스
# ──────────────────────────────────────────────

class PumpCurve:
    """
    ! 펌프 성능 곡선 (P-Q Curve) — 유량별 양정을 보간합니다.
    * cubic spline 보간으로 부드러운 곡선 생성
    """

    def __init__(self, name: str, pq_points: list, efficiency: float):
        self.name = name
        self.efficiency = efficiency
        self.pq_points = pq_points

        flows = np.array([p[0] for p in pq_points], dtype=float)
        heads = np.array([p[1] for p in pq_points], dtype=float)

        self.min_flow = float(flows.min())
        self.max_flow = float(flows.max())

        kind = 'cubic' if len(pq_points) >= 4 else 'quadratic'
        self.interp = interp1d(flows, heads, kind=kind, fill_value='extrapolate')

    def head_at_flow(self, Q_lpm: float) -> float:
        return float(self.interp(Q_lpm))

    def get_curve_points(self, n_points: int = 100) -> Tuple[np.ndarray, np.ndarray]:
        Q = np.linspace(self.min_flow, self.max_flow, n_points)
        H = self.interp(Q)
        return Q, H


def load_pump(model_name: str) -> PumpCurve:
    data = PUMP_DATABASE[model_name]
    return PumpCurve(
        name=model_name,
        pq_points=data["pq_points"],
        efficiency=data["efficiency"],
    )


# ──────────────────────────────────────────────
# ? 동적 시스템 저항 곡선 클래스
# ──────────────────────────────────────────────

class DynamicSystemCurve:
    """
    ! 동적 배관망의 시스템 저항 곡선

    * 다양한 유량에서 동적 시스템 전체(교차배관 + n개 가지배관)의
      최악 가지배관 총 손실 수두 + 최소 말단 수두를 반환
    * 직관 용접 비드(beads_per_branch)를 균등 배치로 포함
    """

    def __init__(
        self,
        num_branches: int,
        heads_per_branch: int,
        branch_spacing_m: float,
        head_spacing_m: float,
        bead_heights_2d: List[List[float]],
        K1_base: float = K1_BASE,
        K2_val: float = K2,
        K3_val: float = K3,
        beads_per_branch: int = 0,
        bead_height_for_weld_mm: float = 1.5,
        topology: str = "tree",
        relaxation: float = 0.5,
    ):
        self.num_branches = num_branches
        self.heads_per_branch = heads_per_branch
        self.branch_spacing_m = branch_spacing_m
        self.head_spacing_m = head_spacing_m
        self.bead_heights_2d = bead_heights_2d
        self.K1_base = K1_base
        self.K2_val = K2_val
        self.K3_val = K3_val
        self.beads_per_branch = beads_per_branch
        self.bead_height_for_weld_mm = bead_height_for_weld_mm
        self.topology = topology
        self.relaxation = relaxation
        self.min_terminal_head = mpa_to_head(MIN_TERMINAL_PRESSURE_MPA)

        # * 관경 리스트 사전 계산 (유량 무관, 헤드 수 기반)
        from constants import auto_pipe_size
        self._pipe_sizes = [
            auto_pipe_size(heads_per_branch - h)
            for h in range(heads_per_branch)
        ]

        # * 직관 용접 비드 균등 배치 사전 생성 (각 유량에서 재사용)
        if beads_per_branch > 0:
            single_beads = generate_branch_beads(
                heads_per_branch, head_spacing_m, beads_per_branch,
                bead_height_for_weld_mm, self._pipe_sizes, K1_base,
                rng=None,
            )
            self._weld_beads_2d = [list(single_beads) for _ in range(num_branches)]
        else:
            self._weld_beads_2d = None

    def head_at_flow(self, Q_lpm: float) -> float:
        """주어진 유량에서 시스템이 요구하는 총 양정(m)"""
        if Q_lpm <= 0:
            return self.min_terminal_head

        dummy_inlet = 10.0

        if self.topology == "grid":
            from hardy_cross import run_grid_system
            result = run_grid_system(
                num_branches=self.num_branches,
                heads_per_branch=self.heads_per_branch,
                branch_spacing_m=self.branch_spacing_m,
                head_spacing_m=self.head_spacing_m,
                inlet_pressure_mpa=dummy_inlet,
                total_flow_lpm=Q_lpm,
                bead_heights_2d=self.bead_heights_2d,
                K1_base=self.K1_base,
                K2_val=self.K2_val,
                K3_val=self.K3_val,
                weld_beads_2d=self._weld_beads_2d,
                beads_per_branch=self.beads_per_branch,
                bead_height_for_weld_mm=self.bead_height_for_weld_mm,
                relaxation=self.relaxation,
            )
        else:
            system = generate_dynamic_system(
                num_branches=self.num_branches,
                heads_per_branch=self.heads_per_branch,
                branch_spacing_m=self.branch_spacing_m,
                head_spacing_m=self.head_spacing_m,
                inlet_pressure_mpa=dummy_inlet,
                total_flow_lpm=Q_lpm,
                bead_heights_2d=self.bead_heights_2d,
                K1_base=self.K1_base,
                K2_val=self.K2_val,
                weld_beads_2d=self._weld_beads_2d,
            )
            result = calculate_dynamic_system(system, self.K3_val)

        # 최악 가지배관의 총 손실 = 입구 - 말단
        total_loss_mpa = dummy_inlet - result["worst_terminal_mpa"]
        total_loss_head = mpa_to_head(total_loss_mpa)

        return total_loss_head + self.min_terminal_head

    def get_curve_points(
        self, n_points: int = 50, q_max: float = 1500.0
    ) -> Tuple[np.ndarray, np.ndarray]:
        Q = np.linspace(50, q_max, n_points)
        H = np.array([self.head_at_flow(q) for q in Q])
        return Q, H


# ──────────────────────────────────────────────
# ? 레거시 시스템 저항 곡선 (하위 호환)
# ──────────────────────────────────────────────

class SystemCurve:
    """레거시: 단일 가지배관 시스템 저항 곡선"""

    def __init__(
        self,
        bead_heights: List[float],
        fitting_spacing_m: float = DEFAULT_FITTING_SPACING_M,
        K1_base: float = 0.5,
        K2_val: float = 2.5,
        K3_val: float = 1.0,
    ):
        self.bead_heights = bead_heights
        self.fitting_spacing_m = fitting_spacing_m
        self.K1_base = K1_base
        self.K2_val = K2_val
        self.K3_val = K3_val
        self.min_terminal_head = mpa_to_head(MIN_TERMINAL_PRESSURE_MPA)

    def head_at_flow(self, Q_lpm: float) -> float:
        if Q_lpm <= 0:
            return self.min_terminal_head

        dummy_inlet = 10.0
        network = build_default_network(
            inlet_pressure_mpa=dummy_inlet,
            total_flow_lpm=Q_lpm,
            fitting_spacing_m=self.fitting_spacing_m,
            bead_heights=self.bead_heights,
            K1_base=self.K1_base,
            K2_val=self.K2_val,
            K3_val=self.K3_val,
        )
        profile = calculate_pressure_profile(network)
        total_loss_mpa = profile["cumulative_loss_mpa"][-1]
        total_loss_head = mpa_to_head(total_loss_mpa)
        return total_loss_head + self.min_terminal_head

    def get_curve_points(
        self, n_points: int = 50, q_max: float = 1500.0
    ) -> Tuple[np.ndarray, np.ndarray]:
        Q = np.linspace(50, q_max, n_points)
        H = np.array([self.head_at_flow(q) for q in Q])
        return Q, H


# ──────────────────────────────────────────────
# ? 운전점 탐색 (P-Q ∩ 시스템 곡선)
# ──────────────────────────────────────────────

def find_operating_point(pump: PumpCurve, system) -> Optional[dict]:
    """
    ! 펌프 곡선과 시스템 저항 곡선의 교점 (운전점)

    system: SystemCurve 또는 DynamicSystemCurve (둘 다 head_at_flow 메서드 보유)
    """

    def residual(Q):
        return pump.head_at_flow(Q) - system.head_at_flow(Q)

    q_low = pump.min_flow + 1.0
    q_high = pump.max_flow - 1.0

    try:
        r_low = residual(q_low)
        r_high = residual(q_high)

        if r_low * r_high > 0:
            return None

        Q_op = brentq(residual, q_low, q_high, xtol=0.1)
    except (ValueError, RuntimeError):
        return None

    H_op = pump.head_at_flow(Q_op)
    Q_m3s = Q_op / 60000.0
    power_w = RHO * G * Q_m3s * H_op / pump.efficiency
    power_kw = power_w / 1000.0

    return {
        "flow_lpm": round(Q_op, 2),
        "head_m": round(H_op, 2),
        "power_kw": round(power_kw, 3),
    }


# ──────────────────────────────────────────────
# ? 에너지 절감 계산
# ──────────────────────────────────────────────

def calculate_energy_savings(
    op_point_A: dict,
    op_point_B: dict,
    operating_hours_per_year: float = DEFAULT_OPERATING_HOURS_PER_YEAR,
    electricity_rate_krw: float = DEFAULT_ELECTRICITY_RATE_KRW,
) -> dict:
    """! Case A(기존) vs Case B(신기술) 펌프 에너지 비교"""
    delta_kw = op_point_A["power_kw"] - op_point_B["power_kw"]
    delta_head = op_point_A["head_m"] - op_point_B["head_m"]
    annual_kwh = delta_kw * operating_hours_per_year
    annual_krw = annual_kwh * electricity_rate_krw

    return {
        "delta_power_kw": round(delta_kw, 3),
        "delta_head_m": round(delta_head, 2),
        "delta_flow_lpm": round(op_point_B["flow_lpm"] - op_point_A["flow_lpm"], 2),
        "annual_energy_kwh": round(annual_kwh, 1),
        "annual_cost_savings_krw": round(annual_krw, 0),
        "case_A_power_kw": op_point_A["power_kw"],
        "case_B_power_kw": op_point_B["power_kw"],
    }
