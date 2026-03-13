# ! 소화배관 시뮬레이션 — 동적 배관망 생성 및 압력 순회 알고리즘
# * 교차배관(Cross Main) + n개 양방향 가지배관 × m개 헤드 동적 생성
# * 레거시 고정 8헤드 모드도 하위 호환 유지

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from constants import (
    PIPE_ASSIGNMENT, PIPE_DIMENSIONS, NUM_HEADS,
    K1_BASE, K2, K3, K_TEE_RUN, G, RHO,
    K2_WITH_HEAD_FITTING, K2_WITHOUT_HEAD_FITTING, DEFAULT_USE_HEAD_FITTING,
    DEFAULT_REDUCER_MODE, DEFAULT_REDUCER_K_FIXED,
    REDUCER_MODE_CRANE, REDUCER_MODE_SUDDEN, REDUCER_MODE_FIXED, REDUCER_MODE_NONE,
    REDUCER_ANGLES_DEG,
    DEFAULT_INLET_PRESSURE_MPA, DEFAULT_TOTAL_FLOW_LPM,
    DEFAULT_FITTING_SPACING_M, MIN_TERMINAL_PRESSURE_MPA,
    MAX_TERMINAL_PRESSURE_MPA, MAX_VELOCITY_BRANCH_MS, MAX_VELOCITY_OTHER_MS,
    DEFAULT_NUM_BRANCHES, DEFAULT_HEADS_PER_BRANCH,
    DEFAULT_BRANCH_SPACING_M, DEFAULT_HEAD_SPACING_M,
    MAX_BRANCHES, MAX_HEADS_PER_BRANCH,
    DEFAULT_SUPPLY_PIPE_SIZE,
    BRANCH_INLET_CONFIGS, DEFAULT_BRANCH_INLET_CONFIG,
    auto_pipe_size, auto_cross_main_size, get_inner_diameter_m,
)
from hydraulics import (
    velocity_from_flow, reynolds_number, friction_factor,
    major_loss, minor_loss, k_welded_fitting, k_reducer,
    head_to_mpa, mpa_to_head,
)


# ══════════════════════════════════════════════
#  PART 1: 공통 데이터 구조
# ══════════════════════════════════════════════

@dataclass
class PipeSegment:
    """하나의 직관 구간"""
    index: int
    nominal_size: str
    inner_diameter_m: float
    length_m: float


@dataclass
class HeadJunction:
    """하나의 스프링클러 헤드 분기점"""
    index: int
    pipe_segment: PipeSegment
    bead_height_mm: float
    K1_welded: float
    K2_head: float
    head_flow_lpm: float


# ══════════════════════════════════════════════
#  PART 1-B: 레듀서 손실 계산 유틸리티
# ══════════════════════════════════════════════

def _calc_reducer_loss_mpa(
    prev_size: str, curr_size: str, V_downstream: float,
    reducer_mode: str = DEFAULT_REDUCER_MODE,
    reducer_k_fixed: float = DEFAULT_REDUCER_K_FIXED,
) -> float:
    """
    관경 전환 시 레듀서(점축소) 국부 손실 계산 (MPa)

    prev_size      : 상류 관경 (예: "50A")
    curr_size      : 하류 관경 (예: "40A")
    V_downstream   : 하류 유속 (m/s) — K값 기준 속도
    reducer_mode   : "crane" / "sudden" / "fixed" / "none"
    reducer_k_fixed: "fixed" 모드 시 사용할 K값

    출처: Crane Technical Paper 410, ASME B16.9 레듀서 치수
    """
    if reducer_mode == REDUCER_MODE_NONE:
        return 0.0
    if prev_size == curr_size:
        return 0.0

    prev_id_mm = PIPE_DIMENSIONS[prev_size]["id_mm"]
    curr_id_mm = PIPE_DIMENSIONS[curr_size]["id_mm"]
    if curr_id_mm >= prev_id_mm:
        return 0.0  # 확대가 아닌 축소만 계산

    if reducer_mode == REDUCER_MODE_FIXED:
        K_red = reducer_k_fixed
    else:
        theta = REDUCER_ANGLES_DEG.get((prev_size, curr_size), 10.0)
        mode = "sudden" if reducer_mode == REDUCER_MODE_SUDDEN else "crane"
        K_red = k_reducer(prev_id_mm, curr_id_mm, theta, mode)

    return head_to_mpa(minor_loss(K_red, V_downstream))


# ══════════════════════════════════════════════
#  PART 2: 입력 검증 (방어적 프로그래밍)
# ══════════════════════════════════════════════

class ValidationError(Exception):
    """사용자 입력 검증 실패 시 발생하는 예외"""
    pass


def validate_dynamic_inputs(
    num_branches: int,
    heads_per_branch: int,
    branch_spacing_m: float,
    head_spacing_m: float,
    inlet_pressure_mpa: float,
    total_flow_lpm: float,
) -> None:
    """
    ! 동적 배관망 입력값 검증 — 잘못된 값 입력 시 명확한 에러 메시지

    * 0, 음수, 과도한 값에 대한 방어적 프로그래밍
    """
    if not isinstance(num_branches, int) or num_branches < 1:
        raise ValidationError(
            f"가지배관 수는 1 이상의 정수여야 합니다. (입력값: {num_branches})"
        )
    if num_branches > MAX_BRANCHES:
        raise ValidationError(
            f"가지배관 수가 최대 허용치({MAX_BRANCHES})를 초과합니다. (입력값: {num_branches})"
        )
    if not isinstance(heads_per_branch, int) or heads_per_branch < 1:
        raise ValidationError(
            f"가지배관당 헤드 수는 1 이상의 정수여야 합니다. (입력값: {heads_per_branch})"
        )
    if heads_per_branch > MAX_HEADS_PER_BRANCH:
        raise ValidationError(
            f"헤드 수가 최대 허용치({MAX_HEADS_PER_BRANCH})를 초과합니다. (입력값: {heads_per_branch})"
        )
    if branch_spacing_m <= 0:
        raise ValidationError(
            f"가지배관 간격은 양수여야 합니다. (입력값: {branch_spacing_m}m)"
        )
    if head_spacing_m <= 0:
        raise ValidationError(
            f"헤드 간격은 양수여야 합니다. (입력값: {head_spacing_m}m)"
        )
    if inlet_pressure_mpa <= 0:
        raise ValidationError(
            f"입구 압력은 양수여야 합니다. (입력값: {inlet_pressure_mpa} MPa)"
        )
    if total_flow_lpm <= 0:
        raise ValidationError(
            f"설계 유량은 양수여야 합니다. (입력값: {total_flow_lpm} LPM)"
        )


# ══════════════════════════════════════════════
#  PART 3: 동적 배관망 데이터 구조
# ══════════════════════════════════════════════

@dataclass
class BranchPipe:
    """하나의 가지배관 (헤드 m개 포함)"""
    branch_index: int
    num_heads: int
    junctions: List[HeadJunction] = field(default_factory=list)
    branch_flow_lpm: float = 0.0
    # * 아래 필드는 calculate 후에 채워짐
    pipe_sizes: List[str] = field(default_factory=list)
    # * 입구 배관 (65A 등, 교차배관→첫 헤드 사이 추가 구간, 헤드 없음)
    inlet_pipe_size: Optional[str] = None
    inlet_pipe_length_m: float = 0.0


@dataclass
class CrossMainSegment:
    """교차배관의 한 구간 (두 가지배관 분기점 사이)"""
    index: int
    nominal_size: str
    inner_diameter_m: float
    length_m: float
    flow_lpm: float


@dataclass
class DynamicSystem:
    """
    ! 전체 배관 시스템: 교차배관 + n개 가지배관

    구조:
        입구(Riser) ─── [교차배관 65A+] ──┬──┬──┬── ... ──┬──
                                          B1  B2  B3       Bn
                                          │   │   │         │
                                          H1  H1  H1       H1
                                          H2  H2  H2       H2
                                          ..  ..  ..       ..
                                          Hm  Hm  Hm       Hm (최말단)
    """
    inlet_pressure_mpa: float
    total_flow_lpm: float
    num_branches: int
    heads_per_branch: int
    branch_spacing_m: float
    head_spacing_m: float
    cross_main_size: str
    cross_main_segments: List[CrossMainSegment] = field(default_factory=list)
    branches: List[BranchPipe] = field(default_factory=list)
    # * 2D 비드 배열: bead_heights[branch_idx][head_idx]
    bead_heights: List[List[float]] = field(default_factory=list)


# ══════════════════════════════════════════════
#  PART 4: 동적 배관망 생성 알고리즘 (Generator)
# ══════════════════════════════════════════════

def generate_dynamic_system(
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    bead_heights_2d: Optional[List[List[float]]] = None,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    use_head_fitting: bool = DEFAULT_USE_HEAD_FITTING,
    branch_inlet_config: Optional[str] = None,
) -> DynamicSystem:
    """
    ! 사용자 입력 기반 동적 배관망 자동 생성

    use_head_fitting: True → K2=K2_val(2.5), False → K2=K2_WITHOUT_HEAD_FITTING(1.4)
      출처: Crane TP-410, K=60×fT (Tee branch flow)

    알고리즘:
    1. 입력값 검증
    2. 교차배관 구경 자동 선정 (전체 헤드 수 기준)
    3. 교차배관 구간 생성 (n-1개 구간)
    4. 각 가지배관 생성 (m개 헤드, 관경 자동 선정)
    5. 이음쇠 비드(junction bead) 배열 적용

    bead_heights_2d   : [branch_idx][head_idx] = 이음쇠 비드 높이(mm), None이면 0.0
    use_head_fitting  : True=헤드이음쇠 사용(K2=2.5), False=직접연결(K2=1.4)
    """
    # * Step 1: 입력 검증
    validate_dynamic_inputs(
        num_branches, heads_per_branch,
        branch_spacing_m, head_spacing_m,
        inlet_pressure_mpa, total_flow_lpm,
    )

    total_heads = num_branches * heads_per_branch

    # * Step 1.5: 가지배관 분기 구조 설정 파싱
    _cross_main_override = None
    _inlet_pipe = None
    _inlet_pipe_length = 0.0
    if branch_inlet_config and branch_inlet_config in BRANCH_INLET_CONFIGS:
        cfg = BRANCH_INLET_CONFIGS[branch_inlet_config]
        _cross_main_override = cfg.get("cross_main_override")
        _inlet_pipe = cfg.get("inlet_pipe")
        _inlet_pipe_length = cfg.get("inlet_pipe_length_m", 0.3)

    # * Step 2: 비드 배열 초기화
    if bead_heights_2d is None:
        bead_heights_2d = [[0.0] * heads_per_branch for _ in range(num_branches)]

    # * Step 3: 교차배관 구경 선정 (분기 구조 설정에 따라 강제 또는 자동)
    if _cross_main_override:
        cross_main_size = _cross_main_override
    else:
        cross_main_size = auto_cross_main_size(total_heads)
    cross_main_id_m = get_inner_diameter_m(cross_main_size)

    # * Step 4: 각 가지배관으로의 유량 균등 분배
    branch_flow = total_flow_lpm / num_branches
    head_flow = branch_flow / heads_per_branch

    # * Step 5: 교차배관 구간 생성 (입구 → 각 분기점)
    cross_main_segments = []
    for i in range(num_branches):
        remaining_flow = total_flow_lpm - i * branch_flow
        seg = CrossMainSegment(
            index=i,
            nominal_size=cross_main_size,
            inner_diameter_m=cross_main_id_m,
            length_m=branch_spacing_m if i > 0 else 0.0,
            flow_lpm=remaining_flow,
        )
        cross_main_segments.append(seg)

    # * Step 6: 각 가지배관 생성 (반복문으로 동적 Instantiate)
    branches = []
    for b in range(num_branches):
        junctions = []
        pipe_sizes = []

        for h in range(heads_per_branch):
            # 하류 헤드 수 기준 관경 자동 선정
            downstream = heads_per_branch - h
            nom_size = auto_pipe_size(downstream)
            pipe_sizes.append(nom_size)
            id_m = get_inner_diameter_m(nom_size)
            id_mm = PIPE_DIMENSIONS[nom_size]["id_mm"]

            segment = PipeSegment(
                index=h,
                nominal_size=nom_size,
                inner_diameter_m=id_m,
                length_m=head_spacing_m,
            )

            bead_h = bead_heights_2d[b][h]
            K1 = k_welded_fitting(bead_h, id_mm, K1_base)
            K2_actual = K2_val if use_head_fitting else K2_WITHOUT_HEAD_FITTING

            junction = HeadJunction(
                index=h,
                pipe_segment=segment,
                bead_height_mm=bead_h,
                K1_welded=K1,
                K2_head=K2_actual,
                head_flow_lpm=head_flow,
            )
            junctions.append(junction)

        bp = BranchPipe(
            branch_index=b,
            num_heads=heads_per_branch,
            junctions=junctions,
            branch_flow_lpm=branch_flow,
            pipe_sizes=pipe_sizes,
            inlet_pipe_size=_inlet_pipe,
            inlet_pipe_length_m=_inlet_pipe_length,
        )
        branches.append(bp)

    return DynamicSystem(
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        num_branches=num_branches,
        heads_per_branch=heads_per_branch,
        branch_spacing_m=branch_spacing_m,
        head_spacing_m=head_spacing_m,
        cross_main_size=cross_main_size,
        cross_main_segments=cross_main_segments,
        branches=branches,
        bead_heights=bead_heights_2d,
    )


# ══════════════════════════════════════════════
#  PART 5: 동적 시스템 압력 계산
# ══════════════════════════════════════════════

def _calculate_branch_profile(
    branch: BranchPipe,
    branch_inlet_pressure_mpa: float,
    K3_val: float = K3,
    bead_velocity_model: str = "upstream",
    reducer_mode: str = DEFAULT_REDUCER_MODE,
    reducer_k_fixed: float = DEFAULT_REDUCER_K_FIXED,
) -> dict:
    """
    단일 가지배관의 압력 프로파일 계산 (내부 함수)

    branch_inlet_pressure_mpa: 교차배관 분기점에서의 압력 (교차배관 손실 반영 후)
    bead_velocity_model: "upstream" = K_eff × V² (D/D_eff)^4 모델
                         "constriction" = K_eff × V_eff² (D/D_eff)^8 모델
    reducer_mode: 레듀서 손실 모드 (crane/sudden/fixed/none)
      출처: Crane Technical Paper 410, ASME B16.9
    """
    n = branch.num_heads
    total_flow = branch.branch_flow_lpm
    head_flow = total_flow / n

    positions = list(range(n + 1))
    pressures = []
    cumulative_loss = []
    seg_details = []

    current_p = branch_inlet_pressure_mpa
    current_loss = 0.0
    pressures.append(current_p)
    cumulative_loss.append(0.0)

    # * K3 분기 입구 손실 + 입구 배관 마찰 손실
    inlet_pipe_friction_mpa = 0.0
    if branch.inlet_pipe_size:
        # 입구 배관(예: 65A)이 있는 경우 → 65A 유속으로 K3 + 65A 마찰 손실
        inlet_id_m = get_inner_diameter_m(branch.inlet_pipe_size)
        V_inlet = velocity_from_flow(total_flow, inlet_id_m)
        K3_loss = head_to_mpa(minor_loss(K3_val, V_inlet))
        current_p -= K3_loss
        current_loss += K3_loss
        # 입구 배관 직관 마찰 손실 (Darcy-Weisbach)
        Re_inlet = reynolds_number(V_inlet, inlet_id_m)
        f_inlet = friction_factor(Re_inlet, D=inlet_id_m)
        inlet_pipe_friction_mpa = head_to_mpa(
            major_loss(f_inlet, branch.inlet_pipe_length_m, inlet_id_m, V_inlet)
        )
        current_p -= inlet_pipe_friction_mpa
        current_loss += inlet_pipe_friction_mpa
    else:
        # 입구 배관 없이 직접 분기 → 첫 번째 헤드 구간 유속으로 K3
        first_seg = branch.junctions[0].pipe_segment
        V_inlet = velocity_from_flow(total_flow, first_seg.inner_diameter_m)
        K3_loss = head_to_mpa(minor_loss(K3_val, V_inlet))
        current_p -= K3_loss
        current_loss += K3_loss

    # * 입구 레듀서 손실 (65A→50A 등, 입구관과 첫 헤드 구간 관경이 다를 때)
    inlet_reducer_mpa = 0.0
    if branch.inlet_pipe_size:
        first_size = branch.junctions[0].pipe_segment.nominal_size
        if branch.inlet_pipe_size != first_size:
            first_id_m = branch.junctions[0].pipe_segment.inner_diameter_m
            V_first = velocity_from_flow(total_flow, first_id_m)
            inlet_reducer_mpa = _calc_reducer_loss_mpa(
                branch.inlet_pipe_size, first_size, V_first,
                reducer_mode, reducer_k_fixed)
            current_p -= inlet_reducer_mpa
            current_loss += inlet_reducer_mpa

    # * 3항 분리 누적 변수 초기화
    total_loss_pipe = inlet_pipe_friction_mpa    # ΔP_pipe: 배관 마찰 손실
    total_loss_fitting = K3_loss + inlet_reducer_mpa  # ΔP_fitting: 이음쇠 기본 손실 (K3 + 입구 레듀서)
    total_loss_bead = 0.0                        # ΔP_bead: 비드 추가 손실

    for i, junc in enumerate(branch.junctions):
        seg = junc.pipe_segment
        segment_flow = total_flow - (i * head_flow)
        V = velocity_from_flow(segment_flow, seg.inner_diameter_m)
        Re = reynolds_number(V, seg.inner_diameter_m)
        f = friction_factor(Re, D=seg.inner_diameter_m)

        p_major = head_to_mpa(major_loss(f, seg.length_m, seg.inner_diameter_m, V))

        # * 비드 K1 손실: 속도 기준 선택
        if bead_velocity_model == "constriction" and junc.bead_height_mm > 0:
            D_eff_m = seg.inner_diameter_m - 2.0 * junc.bead_height_mm / 1000.0
            V_bead = velocity_from_flow(segment_flow, D_eff_m) if D_eff_m > 0 else V
            p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V_bead))
        else:
            p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V))
        p_K2 = head_to_mpa(minor_loss(junc.K2_head, V))

        # * K1 → 이음쇠 기본(K_base) + 비드 추가분 분리
        p_K1_base = head_to_mpa(minor_loss(K1_BASE, V))
        p_K1_bead = max(0.0, p_K1 - p_K1_base)

        # * 레듀서 국부 손실 (관경 전환 시) — Crane TP-410 / ASME B16.9
        p_reducer = 0.0
        if i > 0:
            prev_size = branch.junctions[i - 1].pipe_segment.nominal_size
            curr_size = seg.nominal_size
            if prev_size != curr_size:
                p_reducer = _calc_reducer_loss_mpa(
                    prev_size, curr_size, V, reducer_mode, reducer_k_fixed)

        total_seg_loss = p_major + p_K1 + p_K2 + p_reducer
        current_p -= total_seg_loss
        current_loss += total_seg_loss

        # * 3항 분리 누적
        total_loss_pipe += p_major
        total_loss_fitting += p_K1_base + p_K2 + p_reducer
        total_loss_bead += p_K1_bead

        pressures.append(current_p)
        cumulative_loss.append(current_loss)
        seg_details.append({
            "head_number": i + 1,
            "pipe_size": seg.nominal_size,
            "inner_diameter_mm": round(seg.inner_diameter_m * 1000, 2),
            "flow_lpm": round(segment_flow, 2),
            "velocity_ms": round(V, 4),
            "reynolds": round(Re, 0),
            "friction_factor": round(f, 6),
            "major_loss_mpa": round(p_major, 6),
            "K1_value": round(junc.K1_welded, 4),
            "K1_loss_mpa": round(p_K1, 6),
            "K2_loss_mpa": round(p_K2, 6),
            "reducer_loss_mpa": round(p_reducer, 6),
            "total_seg_loss_mpa": round(total_seg_loss, 6),
            "pressure_after_mpa": round(current_p, 6),
            "bead_height_mm": junc.bead_height_mm,
        })

    return {
        "positions": positions,
        "pressures_mpa": pressures,
        "cumulative_loss_mpa": cumulative_loss,
        "terminal_pressure_mpa": pressures[-1],
        "K3_loss_mpa": K3_loss,
        "inlet_pipe_friction_mpa": inlet_pipe_friction_mpa,
        "inlet_pipe_size": branch.inlet_pipe_size,
        "segment_details": seg_details,
        # 3항 분리 손실 (가지배관 내)
        "loss_pipe_mpa": round(total_loss_pipe, 6),
        "loss_fitting_mpa": round(total_loss_fitting, 6),
        "loss_bead_mpa": round(total_loss_bead, 6),
    }


def calculate_dynamic_system(
    system: DynamicSystem,
    K3_val: float = K3,
    equipment_k_factors: Optional[dict] = None,
    supply_pipe_size: str = DEFAULT_SUPPLY_PIPE_SIZE,
    bead_velocity_model: str = "upstream",
    reducer_mode: str = DEFAULT_REDUCER_MODE,
    reducer_k_fixed: float = DEFAULT_REDUCER_K_FIXED,
) -> dict:
    """
    ! 전체 동적 시스템 압력 계산

    알고리즘:
    0. (신규) 공급배관 밸브/기기류 국부 손실 차감
    1. 교차배관 구간별 손실 누적 → 각 가지배관 분기점 압력 산출
    2. 각 가지배관별 압력 프로파일 계산
    3. 최악 가지배관 (최저 말단 압력) 식별

    equipment_k_factors : {"밸브이름": {"K": float, "qty": int}, ...} 또는 None
    supply_pipe_size    : 공급배관(라이저) 구경 (기본 "100A")

    반환:
        branch_inlet_pressures : 각 가지배관 분기점 압력
        branch_profiles        : 각 가지배관의 상세 프로파일
        cross_main_losses      : 교차배관 구간별 손실
        worst_branch_index     : 최저 말단 압력 가지배관 인덱스
        worst_terminal_mpa     : 최저 말단 압력
        all_terminal_pressures : 모든 가지배관의 말단 압력 리스트
        equipment_loss_mpa     : 밸브/기기류 총 손실 (MPa)
        equipment_loss_details : 각 밸브별 손실 상세
    """
    n_branches = system.num_branches
    branch_flow = system.total_flow_lpm / n_branches

    # ── Step 0: 밸브/기기류 국부 손실 계산 (공급배관 라이저) ──
    equipment_loss_mpa = 0.0
    equipment_loss_details = []
    if equipment_k_factors:
        supply_id_m = get_inner_diameter_m(supply_pipe_size)
        V_supply = velocity_from_flow(system.total_flow_lpm, supply_id_m)
        for name, info in equipment_k_factors.items():
            K_val = info["K"]
            qty = info.get("qty", 1)
            single_loss = head_to_mpa(minor_loss(K_val, V_supply))
            total_loss = single_loss * qty
            equipment_loss_mpa += total_loss
            equipment_loss_details.append({
                "name": name,
                "K": K_val,
                "qty": qty,
                "loss_mpa": round(total_loss, 6),
            })

    # ── Step 1: 교차배관 손실 계산 ──
    cross_main_id_m = get_inner_diameter_m(system.cross_main_size)
    branch_inlet_pressures = []
    cross_main_losses = []
    cm_cumulative_loss = 0.0
    current_cm_pressure = system.inlet_pressure_mpa - equipment_loss_mpa

    # 교차배관 3항 분리 누적
    cm_loss_pipe = 0.0      # 교차배관 마찰 손실
    cm_loss_fitting = 0.0   # 교차배관 Tee-Run 손실 + 밸브 손실

    cm_loss_fitting += equipment_loss_mpa  # 밸브류는 이음쇠 손실로 분류

    for i in range(n_branches):
        seg = system.cross_main_segments[i]

        if i > 0:
            # 교차배관 직진 구간: 주손실 + Tee-Run 부차손실
            remaining_flow = system.total_flow_lpm - i * branch_flow
            V_cm = velocity_from_flow(remaining_flow, cross_main_id_m)
            Re_cm = reynolds_number(V_cm, cross_main_id_m)
            f_cm = friction_factor(Re_cm, D=cross_main_id_m)

            p_cm_major = head_to_mpa(
                major_loss(f_cm, system.branch_spacing_m, cross_main_id_m, V_cm)
            )
            p_cm_tee = head_to_mpa(minor_loss(K_TEE_RUN, V_cm))
            cm_seg_loss = p_cm_major + p_cm_tee
            cm_loss_pipe += p_cm_major
            cm_loss_fitting += p_cm_tee
        else:
            cm_seg_loss = 0.0

        cm_cumulative_loss += cm_seg_loss
        current_cm_pressure -= cm_seg_loss
        branch_inlet_pressures.append(current_cm_pressure)
        cross_main_losses.append(cm_seg_loss)

    # ── Step 2: 각 가지배관 압력 프로파일 ──
    branch_profiles = []
    all_terminal_pressures = []

    for b in range(n_branches):
        profile = _calculate_branch_profile(
            system.branches[b],
            branch_inlet_pressures[b],
            K3_val,
            bead_velocity_model=bead_velocity_model,
            reducer_mode=reducer_mode,
            reducer_k_fixed=reducer_k_fixed,
        )
        branch_profiles.append(profile)
        all_terminal_pressures.append(profile["terminal_pressure_mpa"])

    # ── Step 3: 최악 가지배관 식별 ──
    worst_idx = int(min(range(n_branches), key=lambda i: all_terminal_pressures[i]))
    worst_terminal = all_terminal_pressures[worst_idx]

    # 최악 가지배관의 3항 분리 + 교차배관/밸브 손실 합산
    wp = branch_profiles[worst_idx]
    system_loss_pipe = cm_loss_pipe + wp.get("loss_pipe_mpa", 0.0)
    system_loss_fitting = cm_loss_fitting + wp.get("loss_fitting_mpa", 0.0)
    system_loss_bead = wp.get("loss_bead_mpa", 0.0)

    return {
        "branch_inlet_pressures": branch_inlet_pressures,
        "branch_profiles": branch_profiles,
        "cross_main_losses": cross_main_losses,
        "cross_main_cumulative": cm_cumulative_loss,
        "worst_branch_index": worst_idx,
        "worst_terminal_mpa": worst_terminal,
        "all_terminal_pressures": all_terminal_pressures,
        "total_heads": system.num_branches * system.heads_per_branch,
        "cross_main_size": system.cross_main_size,
        "equipment_loss_mpa": equipment_loss_mpa,
        "equipment_loss_details": equipment_loss_details,
        # 3항 분리 손실 (최악 경로: 교차배관 + 최악 가지배관 전체)
        "loss_pipe_mpa": round(system_loss_pipe, 6),
        "loss_fitting_mpa": round(system_loss_fitting, 6),
        "loss_bead_mpa": round(system_loss_bead, 6),
    }


# ══════════════════════════════════════════════
#  PART 5.5: 수리계산 직접 역산 (ΔP 산출)
# ══════════════════════════════════════════════

def calculate_system_delta_p(
    total_flow_lpm: float,
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    bead_height_mm: float = 0.0,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
    equipment_k_factors: Optional[dict] = None,
    supply_pipe_size: str = DEFAULT_SUPPLY_PIPE_SIZE,
    branch_inlet_config: Optional[str] = None,
    use_head_fitting: bool = DEFAULT_USE_HEAD_FITTING,
    reducer_mode: str = DEFAULT_REDUCER_MODE,
    reducer_k_fixed: float = DEFAULT_REDUCER_K_FIXED,
) -> dict:
    """
    ! DynamicSystem 객체 없이 수식만으로 시스템 총 ΔP 직접 계산

    calculate_dynamic_system()과 독립적인 코드 경로로,
    교차 검증(수리계산 vs 시뮬레이션)의 근거가 됩니다.

    알고리즘:
    1. 장비 K-factor 손실 (공급배관 유속 기준)
    2. 교차배관 손실 (최악 경로 B#4까지 누적)
    3. 분기 입구 손실 (K3 + 입구관 마찰 + 입구 레듀서)
    4. 가지배관 손실 (8헤드 순회, 관경 자동 선정 + 관경 전환 레듀서)

    반환: delta_p 항목별 분리 dict
    """
    branch_flow = total_flow_lpm / num_branches
    head_flow = branch_flow / heads_per_branch
    total_heads = num_branches * heads_per_branch

    # ── 분기 구조 설정 파싱 ──
    _cross_main_override = None
    _inlet_pipe = None
    _inlet_pipe_length = 0.0
    if branch_inlet_config and branch_inlet_config in BRANCH_INLET_CONFIGS:
        cfg = BRANCH_INLET_CONFIGS[branch_inlet_config]
        _cross_main_override = cfg.get("cross_main_override")
        _inlet_pipe = cfg.get("inlet_pipe")
        _inlet_pipe_length = cfg.get("inlet_pipe_length_m", 0.3)

    # ── 교차배관 구경 결정 ──
    if _cross_main_override:
        cross_main_size = _cross_main_override
    else:
        cross_main_size = auto_cross_main_size(total_heads)
    cross_main_id_m = get_inner_diameter_m(cross_main_size)

    # 3항 분리 누적 변수
    dp_pipe = 0.0      # 배관 마찰 손실
    dp_fitting = 0.0   # 이음쇠 기본 손실
    dp_bead = 0.0      # 비드 추가 손실
    dp_equipment = 0.0  # 장비류 손실

    # ── Step 1: 장비 K-factor 손실 ──
    if equipment_k_factors:
        supply_id_m = get_inner_diameter_m(supply_pipe_size)
        V_supply = velocity_from_flow(total_flow_lpm, supply_id_m)
        for name, info in equipment_k_factors.items():
            K_val = info["K"]
            qty = info.get("qty", 1)
            dp_equipment += head_to_mpa(minor_loss(K_val, V_supply)) * qty
    dp_fitting += dp_equipment  # 장비류는 이음쇠 손실로 분류

    # ── Step 2: 교차배관 손실 (최악 경로 = 마지막 가지배관) ──
    for i in range(1, num_branches):
        remaining_flow = total_flow_lpm - i * branch_flow
        V_cm = velocity_from_flow(remaining_flow, cross_main_id_m)
        Re_cm = reynolds_number(V_cm, cross_main_id_m)
        f_cm = friction_factor(Re_cm, D=cross_main_id_m)
        p_cm_major = head_to_mpa(major_loss(f_cm, branch_spacing_m, cross_main_id_m, V_cm))
        p_cm_tee = head_to_mpa(minor_loss(K_TEE_RUN, V_cm))
        dp_pipe += p_cm_major
        dp_fitting += p_cm_tee

    # ── Step 3: 분기 입구 손실 (K3 + 입구관 마찰) ──
    # 가지배관 관경 자동 선정 (분석 경로용)
    pipe_sizes = []
    for h in range(heads_per_branch):
        downstream = heads_per_branch - h
        pipe_sizes.append(auto_pipe_size(downstream))

    if _inlet_pipe:
        inlet_id_m = get_inner_diameter_m(_inlet_pipe)
        V_inlet = velocity_from_flow(branch_flow, inlet_id_m)
        K3_loss = head_to_mpa(minor_loss(K3_val, V_inlet))
        dp_fitting += K3_loss
        # 입구관 직관 마찰 손실
        Re_inlet = reynolds_number(V_inlet, inlet_id_m)
        f_inlet = friction_factor(Re_inlet, D=inlet_id_m)
        inlet_pipe_friction = head_to_mpa(
            major_loss(f_inlet, _inlet_pipe_length, inlet_id_m, V_inlet)
        )
        dp_pipe += inlet_pipe_friction
        # 입구 레듀서 (65A→50A 등)
        if _inlet_pipe != pipe_sizes[0]:
            first_id_m_temp = get_inner_diameter_m(pipe_sizes[0])
            V_first_temp = velocity_from_flow(branch_flow, first_id_m_temp)
            dp_fitting += _calc_reducer_loss_mpa(
                _inlet_pipe, pipe_sizes[0], V_first_temp, reducer_mode, reducer_k_fixed)
    else:
        first_id_m = get_inner_diameter_m(pipe_sizes[0])
        V_inlet = velocity_from_flow(branch_flow, first_id_m)
        K3_loss = head_to_mpa(minor_loss(K3_val, V_inlet))
        dp_fitting += K3_loss

    # K2 실제값 결정
    K2_actual = K2_val if use_head_fitting else K2_WITHOUT_HEAD_FITTING

    # ── Step 4: 가지배관 순회 (8헤드) + 관경 전환 레듀서 ──
    for i in range(heads_per_branch):
        seg_size = pipe_sizes[i]
        seg_id_m = get_inner_diameter_m(seg_size)
        seg_id_mm = PIPE_DIMENSIONS[seg_size]["id_mm"]
        segment_flow = branch_flow - i * head_flow
        V = velocity_from_flow(segment_flow, seg_id_m)
        Re = reynolds_number(V, seg_id_m)
        f = friction_factor(Re, D=seg_id_m)

        # 주손실 (직관 마찰)
        p_major = head_to_mpa(major_loss(f, head_spacing_m, seg_id_m, V))
        dp_pipe += p_major

        # 이음쇠 K1 손실 (비드 포함)
        K1 = k_welded_fitting(bead_height_mm, seg_id_mm, K1_base)
        p_K1 = head_to_mpa(minor_loss(K1, V))
        p_K1_base = head_to_mpa(minor_loss(K1_base, V))
        p_K1_bead = max(0.0, p_K1 - p_K1_base)
        dp_fitting += p_K1_base
        dp_bead += p_K1_bead

        # 헤드 K2 손실
        p_K2 = head_to_mpa(minor_loss(K2_actual, V))
        dp_fitting += p_K2

        # 레듀서 손실 (관경 전환 시) — Crane TP-410
        if i > 0 and pipe_sizes[i] != pipe_sizes[i - 1]:
            dp_fitting += _calc_reducer_loss_mpa(
                pipe_sizes[i - 1], pipe_sizes[i], V, reducer_mode, reducer_k_fixed)

    dp_total = dp_pipe + dp_fitting + dp_bead

    return {
        "delta_p_total_mpa": round(dp_total, 6),
        "delta_p_pipe_mpa": round(dp_pipe, 6),
        "delta_p_fitting_mpa": round(dp_fitting, 6),
        "delta_p_bead_mpa": round(dp_bead, 6),
        "delta_p_equipment_mpa": round(dp_equipment, 6),
        "worst_branch_index": num_branches - 1,
        "method": "analytical",
    }


# ══════════════════════════════════════════════
#  PART 6: 동적 시스템 Case A vs B 비교
# ══════════════════════════════════════════════

def compare_dynamic_cases(
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    bead_height_existing: float = 1.5,
    bead_height_new: float = 0.0,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
    equipment_k_factors: Optional[dict] = None,
    supply_pipe_size: str = DEFAULT_SUPPLY_PIPE_SIZE,
    branch_inlet_config: str = None,
    use_head_fitting: bool = DEFAULT_USE_HEAD_FITTING,
    reducer_mode: str = DEFAULT_REDUCER_MODE,
    reducer_k_fixed: float = DEFAULT_REDUCER_K_FIXED,
) -> dict:
    """
    ! 동적 시스템에서 Case A(기존) vs Case B(신기술) 비교

    * Case A: 이음쇠 비드(bead_height_existing)
    * Case B: 비드 없음(신기술)
    반환: 양쪽 전체 시스템 결과, 최악 가지배관 프로파일, 개선율, Pass/Fail
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
        use_head_fitting=use_head_fitting,
    )

    beads_A = [[bead_height_existing] * heads_per_branch for _ in range(num_branches)]
    beads_B = [[bead_height_new] * heads_per_branch for _ in range(num_branches)]

    sys_A = generate_dynamic_system(
        bead_heights_2d=beads_A,
        branch_inlet_config=branch_inlet_config,
        **common,
    )
    sys_B = generate_dynamic_system(
        bead_heights_2d=beads_B,
        branch_inlet_config=branch_inlet_config,
        **common,
    )

    result_A = calculate_dynamic_system(
        sys_A, K3_val, equipment_k_factors, supply_pipe_size,
        reducer_mode=reducer_mode, reducer_k_fixed=reducer_k_fixed)
    result_B = calculate_dynamic_system(
        sys_B, K3_val, equipment_k_factors, supply_pipe_size,
        reducer_mode=reducer_mode, reducer_k_fixed=reducer_k_fixed)

    term_A = result_A["worst_terminal_mpa"]
    term_B = result_B["worst_terminal_mpa"]

    if term_A != 0:
        improvement_pct = (term_B - term_A) / abs(term_A) * 100.0
    else:
        improvement_pct = 0.0

    # * 최악 가지배관의 상세 프로파일 (차트용)
    worst_idx_A = result_A["worst_branch_index"]
    worst_idx_B = result_B["worst_branch_index"]

    return {
        "system_A": result_A,
        "system_B": result_B,
        "case_A": result_A["branch_profiles"][worst_idx_A],
        "case_B": result_B["branch_profiles"][worst_idx_B],
        "terminal_A_mpa": term_A,
        "terminal_B_mpa": term_B,
        "improvement_pct": improvement_pct,
        "pass_fail_A": term_A >= MIN_TERMINAL_PRESSURE_MPA,
        "pass_fail_B": term_B >= MIN_TERMINAL_PRESSURE_MPA,
        "worst_branch_A": worst_idx_A,
        "worst_branch_B": worst_idx_B,
        "cross_main_size": result_A["cross_main_size"],
        "total_heads": result_A["total_heads"],
    }


# ══════════════════════════════════════════════
#  PART 7: 레거시 호환 함수 (기존 코드 지원)
# ══════════════════════════════════════════════

@dataclass
class BranchNetwork:
    """레거시: 교차배관에서 말단 헤드까지의 단일 가지배관"""
    inlet_pressure_mpa: float
    total_flow_lpm: float
    K3_branch_entry: float
    junctions: List[HeadJunction] = field(default_factory=list)


def build_default_network(
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    fitting_spacing_m: float = DEFAULT_FITTING_SPACING_M,
    bead_heights: Optional[List[float]] = None,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
) -> BranchNetwork:
    """레거시: 고정 8헤드 배관 네트워크 생성 (하위 호환)"""
    if bead_heights is None:
        bead_heights = [0.0] * NUM_HEADS
    if len(bead_heights) != NUM_HEADS:
        raise ValueError(f"bead_heights must have {NUM_HEADS} elements, got {len(bead_heights)}")

    head_flow_each = total_flow_lpm / NUM_HEADS
    junctions = []

    for i in range(NUM_HEADS):
        nom_size = PIPE_ASSIGNMENT[i]
        id_m = get_inner_diameter_m(nom_size)
        id_mm = PIPE_DIMENSIONS[nom_size]["id_mm"]

        segment = PipeSegment(
            index=i, nominal_size=nom_size,
            inner_diameter_m=id_m, length_m=fitting_spacing_m,
        )
        K1 = k_welded_fitting(bead_heights[i], id_mm, K1_base)
        junction = HeadJunction(
            index=i, pipe_segment=segment,
            bead_height_mm=bead_heights[i],
            K1_welded=K1, K2_head=K2_val,
            head_flow_lpm=head_flow_each,
        )
        junctions.append(junction)

    return BranchNetwork(
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        K3_branch_entry=K3_val,
        junctions=junctions,
    )


def calculate_pressure_profile(network: BranchNetwork) -> dict:
    """레거시: 단일 가지배관 압력 순회 (하위 호환)"""
    n = len(network.junctions)
    total_flow = network.total_flow_lpm
    head_flow = total_flow / n

    positions = list(range(n + 1))
    pressures = []
    cumulative_loss = []
    seg_major_losses = []
    seg_K1_losses = []
    seg_K2_losses = []
    velocities = []
    re_numbers = []
    f_factors = []
    segment_details = []

    current_pressure_mpa = network.inlet_pressure_mpa
    current_loss_mpa = 0.0
    pressures.append(current_pressure_mpa)
    cumulative_loss.append(0.0)

    first_seg = network.junctions[0].pipe_segment
    V_inlet = velocity_from_flow(total_flow, first_seg.inner_diameter_m)
    K3_loss_head = minor_loss(network.K3_branch_entry, V_inlet)
    K3_loss_mpa = head_to_mpa(K3_loss_head)
    current_pressure_mpa -= K3_loss_mpa
    current_loss_mpa += K3_loss_mpa

    for i, junc in enumerate(network.junctions):
        seg = junc.pipe_segment
        segment_flow = total_flow - (i * head_flow)
        V = velocity_from_flow(segment_flow, seg.inner_diameter_m)
        Re = reynolds_number(V, seg.inner_diameter_m)
        f = friction_factor(Re, D=seg.inner_diameter_m)

        h_major = major_loss(f, seg.length_m, seg.inner_diameter_m, V)
        p_major = head_to_mpa(h_major)
        h_K1 = minor_loss(junc.K1_welded, V)
        p_K1 = head_to_mpa(h_K1)
        h_K2 = minor_loss(junc.K2_head, V)
        p_K2 = head_to_mpa(h_K2)

        total_seg_loss = p_major + p_K1 + p_K2
        current_pressure_mpa -= total_seg_loss
        current_loss_mpa += total_seg_loss

        pressures.append(current_pressure_mpa)
        cumulative_loss.append(current_loss_mpa)
        seg_major_losses.append(p_major)
        seg_K1_losses.append(p_K1)
        seg_K2_losses.append(p_K2)
        velocities.append(V)
        re_numbers.append(Re)
        f_factors.append(f)

        segment_details.append({
            "head_number": i + 1,
            "pipe_size": seg.nominal_size,
            "inner_diameter_mm": round(seg.inner_diameter_m * 1000, 2),
            "flow_lpm": segment_flow,
            "velocity_ms": round(V, 4),
            "reynolds": round(Re, 0),
            "friction_factor": round(f, 6),
            "major_loss_mpa": round(p_major, 6),
            "K1_value": round(junc.K1_welded, 4),
            "K1_loss_mpa": round(p_K1, 6),
            "K2_loss_mpa": round(p_K2, 6),
            "total_seg_loss_mpa": round(total_seg_loss, 6),
            "pressure_after_mpa": round(current_pressure_mpa, 6),
            "bead_height_mm": junc.bead_height_mm,
        })

    return {
        "positions": positions,
        "pressures_mpa": pressures,
        "cumulative_loss_mpa": cumulative_loss,
        "major_losses_mpa": seg_major_losses,
        "minor_losses_K1_mpa": seg_K1_losses,
        "minor_losses_K2_mpa": seg_K2_losses,
        "velocities_ms": velocities,
        "reynolds": re_numbers,
        "friction_factors": f_factors,
        "terminal_pressure_mpa": pressures[-1],
        "K3_loss_mpa": K3_loss_mpa,
        "segment_details": segment_details,
    }


def compare_cases(
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    fitting_spacing_m: float = DEFAULT_FITTING_SPACING_M,
    bead_height_existing: float = 1.5,
    bead_height_new: float = 0.0,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
) -> dict:
    """레거시: 고정 8헤드 Case A vs B 비교 (하위 호환)"""
    bead_A = [bead_height_existing] * NUM_HEADS
    bead_B = [bead_height_new] * NUM_HEADS

    network_A = build_default_network(
        inlet_pressure_mpa, total_flow_lpm, fitting_spacing_m,
        bead_A, K1_base, K2_val, K3_val,
    )
    network_B = build_default_network(
        inlet_pressure_mpa, total_flow_lpm, fitting_spacing_m,
        bead_B, K1_base, K2_val, K3_val,
    )

    profile_A = calculate_pressure_profile(network_A)
    profile_B = calculate_pressure_profile(network_B)

    term_A = profile_A["terminal_pressure_mpa"]
    term_B = profile_B["terminal_pressure_mpa"]

    if term_A != 0:
        improvement_pct = (term_B - term_A) / abs(term_A) * 100.0
    else:
        improvement_pct = 0.0

    pass_fail_A = term_A >= MIN_TERMINAL_PRESSURE_MPA
    pass_fail_B = term_B >= MIN_TERMINAL_PRESSURE_MPA

    return {
        "case_A": profile_A,
        "case_B": profile_B,
        "terminal_A_mpa": term_A,
        "terminal_B_mpa": term_B,
        "improvement_pct": improvement_pct,
        "pass_fail_A": pass_fail_A,
        "pass_fail_B": pass_fail_B,
    }


# ══════════════════════════════════════════════
#  PART 8: 토폴로지 라우팅 (Tree / Grid 분기)
# ══════════════════════════════════════════════

def compare_dynamic_cases_with_topology(
    topology: str = "tree",
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    bead_height_existing: float = 1.5,
    bead_height_new: float = 0.0,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
    use_head_fitting: bool = DEFAULT_USE_HEAD_FITTING,
    reducer_mode: str = DEFAULT_REDUCER_MODE,
    reducer_k_fixed: float = DEFAULT_REDUCER_K_FIXED,
    relaxation: float = 0.5,
    equipment_k_factors: Optional[dict] = None,
    supply_pipe_size: str = DEFAULT_SUPPLY_PIPE_SIZE,
    branch_inlet_config: str = None,
) -> dict:
    """
    ! 토폴로지(Tree/Grid) 분기가 있는 Case A vs B 비교

    * topology="tree": 기존 compare_dynamic_cases() 호출
    * topology="grid": hardy_cross 모듈의 run_grid_system() 사용
    * 반환 형식은 동일하여 UI 코드 변경 최소화
    """
    if topology == "tree":
        return compare_dynamic_cases(
            num_branches=num_branches,
            heads_per_branch=heads_per_branch,
            branch_spacing_m=branch_spacing_m,
            head_spacing_m=head_spacing_m,
            inlet_pressure_mpa=inlet_pressure_mpa,
            total_flow_lpm=total_flow_lpm,
            bead_height_existing=bead_height_existing,
            bead_height_new=bead_height_new,
            K1_base=K1_base,
            K2_val=K2_val,
            K3_val=K3_val,
            use_head_fitting=use_head_fitting,
            reducer_mode=reducer_mode,
            reducer_k_fixed=reducer_k_fixed,
            equipment_k_factors=equipment_k_factors,
            supply_pipe_size=supply_pipe_size,
            branch_inlet_config=branch_inlet_config,
        )

    # * Grid 모드: Hardy-Cross 기반 비교
    from hardy_cross import run_grid_system

    common = dict(
        num_branches=num_branches,
        heads_per_branch=heads_per_branch,
        branch_spacing_m=branch_spacing_m,
        head_spacing_m=head_spacing_m,
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        K1_base=K1_base,
        K2_val=K2_val,
        K3_val=K3_val,
        use_head_fitting=use_head_fitting,
        reducer_mode=reducer_mode,
        reducer_k_fixed=reducer_k_fixed,
    )

    beads_A = [[bead_height_existing] * heads_per_branch for _ in range(num_branches)]
    beads_B = [[bead_height_new] * heads_per_branch for _ in range(num_branches)]

    result_A = run_grid_system(
        bead_heights_2d=beads_A,
        relaxation=relaxation,
        equipment_k_factors=equipment_k_factors,
        supply_pipe_size=supply_pipe_size,
        **common,
    )
    result_B = run_grid_system(
        bead_heights_2d=beads_B,
        relaxation=relaxation,
        equipment_k_factors=equipment_k_factors,
        supply_pipe_size=supply_pipe_size,
        **common,
    )

    term_A = result_A["worst_terminal_mpa"]
    term_B = result_B["worst_terminal_mpa"]

    if term_A != 0:
        improvement_pct = (term_B - term_A) / abs(term_A) * 100.0
    else:
        improvement_pct = 0.0

    worst_idx_A = result_A["worst_branch_index"]
    worst_idx_B = result_B["worst_branch_index"]

    return {
        "system_A": result_A,
        "system_B": result_B,
        "case_A": result_A["branch_profiles"][worst_idx_A],
        "case_B": result_B["branch_profiles"][worst_idx_B],
        "terminal_A_mpa": term_A,
        "terminal_B_mpa": term_B,
        "improvement_pct": improvement_pct,
        "pass_fail_A": term_A >= MIN_TERMINAL_PRESSURE_MPA,
        "pass_fail_B": term_B >= MIN_TERMINAL_PRESSURE_MPA,
        "worst_branch_A": worst_idx_A,
        "worst_branch_B": worst_idx_B,
        "cross_main_size": result_A["cross_main_size"],
        "total_heads": result_A["total_heads"],
        "topology": "grid",
    }


# ══════════════════════════════════════════════
#  PART 9: NFPC 규정 준수 자동 판정
# ══════════════════════════════════════════════

def check_nfpc_compliance(system_result: dict) -> dict:
    """
    ! NFPC 규정 준수 여부 검사

    검사 항목:
    1. 가지배관 유속: ≤ 6 m/s (MAX_VELOCITY_BRANCH_MS)
    2. 교차배관/기타 유속: ≤ 10 m/s (MAX_VELOCITY_OTHER_MS)
    3. 말단 수압: 0.1 MPa 이상, 1.2 MPa 이하

    system_result: calculate_dynamic_system() 또는 run_grid_system() 반환 dict
    """
    velocity_violations = []
    pressure_violations = []

    # ── 1. 가지배관 유속 검사 (segment_details 기반) ──
    branch_profiles = system_result.get("branch_profiles", [])
    for b_idx, profile in enumerate(branch_profiles):
        seg_details = profile.get("segment_details", [])
        for seg in seg_details:
            v = seg.get("velocity_ms", 0.0)
            if v > MAX_VELOCITY_BRANCH_MS:
                velocity_violations.append({
                    "branch": b_idx,
                    "head": seg.get("head_number", 0),
                    "pipe_size": seg.get("pipe_size", ""),
                    "velocity_ms": v,
                    "limit_ms": MAX_VELOCITY_BRANCH_MS,
                    "pipe_type": "branch",
                })

    # ── 2. 교차배관 유속 검사 ──
    #   교차배관은 segment_details에 포함되지 않으므로
    #   cross_main_size 내경 + 총 유량으로 대표 유속 추정
    cross_main_size = system_result.get("cross_main_size", "65A")
    if cross_main_size in PIPE_DIMENSIONS:
        cm_id_m = PIPE_DIMENSIONS[cross_main_size]["id_mm"] / 1000.0
        cm_area = 3.14159265 * (cm_id_m / 2.0) ** 2
        # 입구 직후 교차배관 유속 = 전체 유량 / 단면적
        total_heads = system_result.get("total_heads", 0)
        if total_heads > 0:
            total_flow_per_head = system_result.get(
                "worst_terminal_mpa", 0
            )  # 대략적 추정이 아닌 실제 유량 사용
            # branch_profiles에서 전체 유량 역산
            total_branch_flow = 0.0
            for profile in branch_profiles:
                segs = profile.get("segment_details", [])
                if segs:
                    total_branch_flow += segs[0].get("flow_lpm", 0.0)
            if total_branch_flow > 0 and cm_area > 0:
                cm_flow_m3s = total_branch_flow / 60000.0
                cm_velocity = cm_flow_m3s / cm_area
                if cm_velocity > MAX_VELOCITY_OTHER_MS:
                    velocity_violations.append({
                        "branch": -1,
                        "head": 0,
                        "pipe_size": cross_main_size,
                        "velocity_ms": round(cm_velocity, 2),
                        "limit_ms": MAX_VELOCITY_OTHER_MS,
                        "pipe_type": "cross_main",
                    })

    # ── 3. 말단 수압 검사 ──
    all_terminals = system_result.get("all_terminal_pressures", [])
    for b_idx, p in enumerate(all_terminals):
        if p < MIN_TERMINAL_PRESSURE_MPA:
            pressure_violations.append({
                "branch": b_idx,
                "type": "under",
                "pressure_mpa": round(p, 4),
                "limit_mpa": MIN_TERMINAL_PRESSURE_MPA,
            })
        elif p > MAX_TERMINAL_PRESSURE_MPA:
            pressure_violations.append({
                "branch": b_idx,
                "type": "over",
                "pressure_mpa": round(p, 4),
                "limit_mpa": MAX_TERMINAL_PRESSURE_MPA,
            })

    return {
        "velocity_violations": velocity_violations,
        "pressure_violations": pressure_violations,
        "is_compliant": len(velocity_violations) == 0 and len(pressure_violations) == 0,
    }
