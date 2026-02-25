# ! 소화배관 시뮬레이션 — 동적 배관망 생성 및 압력 순회 알고리즘
# * 교차배관(Cross Main) + n개 양방향 가지배관 × m개 헤드 동적 생성
# * 레거시 고정 8헤드 모드도 하위 호환 유지

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from constants import (
    PIPE_ASSIGNMENT, PIPE_DIMENSIONS, NUM_HEADS,
    K1_BASE, K2, K3, K_TEE_RUN, G, RHO,
    DEFAULT_INLET_PRESSURE_MPA, DEFAULT_TOTAL_FLOW_LPM,
    DEFAULT_FITTING_SPACING_M, MIN_TERMINAL_PRESSURE_MPA,
    MAX_TERMINAL_PRESSURE_MPA, MAX_VELOCITY_BRANCH_MS, MAX_VELOCITY_OTHER_MS,
    DEFAULT_NUM_BRANCHES, DEFAULT_HEADS_PER_BRANCH,
    DEFAULT_BRANCH_SPACING_M, DEFAULT_HEAD_SPACING_M,
    MAX_BRANCHES, MAX_HEADS_PER_BRANCH,
    DEFAULT_BEADS_PER_BRANCH, MAX_BEADS_PER_BRANCH,
    auto_pipe_size, auto_cross_main_size, get_inner_diameter_m,
)
from hydraulics import (
    velocity_from_flow, reynolds_number, friction_factor,
    major_loss, minor_loss, k_welded_fitting,
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


@dataclass
class WeldBead:
    """
    ! 직관 구간 내 무작위 배치되는 용접 비드 객체

    * 헤드 사이 배관 구간(segment) 내 임의의 위치에 존재
    * 국부 손실(minor loss)을 유발: h = K × V²/2g
    * K값은 비드 높이와 해당 위치의 관경으로 결정
    """
    segment_index: int              # 소속 배관 구간 인덱스 (0 ~ m-1)
    position_in_segment_m: float    # 구간 내 위치 (m, 0 ~ head_spacing_m)
    bead_height_mm: float           # 비드 돌출 높이 (mm)
    K_value: float                  # 산출된 K-factor (= K_base × (D/D_eff)⁴)


def generate_branch_beads(
    heads_per_branch: int,
    head_spacing_m: float,
    num_beads: int,
    bead_height_mm: float,
    pipe_sizes: List[str],
    K1_base: float = K1_BASE,
    rng=None,
) -> List[WeldBead]:
    """
    ! 가지배관 직관 구간 내 용접 비드 자동 생성

    * rng=None → 균등 배치 (deterministic, 정적 비교용)
    * rng=<Generator> → 무작위 배치 (Monte Carlo용)

    알고리즘:
    1. 가지배관 전체 길이 = heads_per_branch × head_spacing_m
    2. num_beads개 위치를 생성 (균등 or 랜덤)
    3. 각 위치 → 소속 구간(segment_index) 결정
    4. 해당 구간의 관경으로 K값 계산
    """
    if num_beads <= 0:
        return []

    total_length = heads_per_branch * head_spacing_m

    if rng is not None:
        # * 무작위 배치 (MC 시뮬레이션)
        positions = sorted(rng.uniform(0, total_length, size=num_beads).tolist())
    else:
        # * 균등 배치 (정적 비교용)
        step = total_length / num_beads
        positions = [step * (i + 0.5) for i in range(num_beads)]

    beads = []
    for pos in positions:
        seg_idx = min(int(pos / head_spacing_m), heads_per_branch - 1)
        pos_in_seg = pos - seg_idx * head_spacing_m
        pipe_size = pipe_sizes[seg_idx]
        pipe_id_mm = PIPE_DIMENSIONS[pipe_size]["id_mm"]
        K = k_welded_fitting(bead_height_mm, pipe_id_mm, K1_base)
        beads.append(WeldBead(
            segment_index=seg_idx,
            position_in_segment_m=round(pos_in_seg, 4),
            bead_height_mm=bead_height_mm,
            K_value=K,
        ))
    return beads


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
    # * 직관 구간 내 용접 비드 목록 (generate_branch_beads로 생성)
    weld_beads: List[WeldBead] = field(default_factory=list)


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
    # * 가지배관당 용접 비드 개수 (직관 구간 내 배치)
    beads_per_branch: int = 0


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
    beads_per_branch: int = 0,
    bead_height_for_weld_mm: float = 1.5,
    weld_beads_2d: Optional[List[List[WeldBead]]] = None,
    rng=None,
) -> DynamicSystem:
    """
    ! 사용자 입력 기반 동적 배관망 자동 생성

    알고리즘:
    1. 입력값 검증
    2. 교차배관 구경 자동 선정 (전체 헤드 수 기준)
    3. 교차배관 구간 생성 (n-1개 구간)
    4. 각 가지배관 생성 (m개 헤드, 관경 자동 선정)
    5. 이음쇠 비드(junction bead) 배열 적용
    6. 직관 구간 용접 비드(weld bead) 생성 및 배치

    bead_heights_2d   : [branch_idx][head_idx] = 이음쇠 비드 높이(mm), None이면 0.0
    beads_per_branch  : 가지배관당 직관 구간 용접 비드 개수 (0이면 미사용)
    bead_height_for_weld_mm : 직관 용접 비드 높이(mm)
    weld_beads_2d     : 사전 생성된 비드 객체 [branch_idx][beads], None이면 자동 생성
    rng               : numpy RNG 객체 (None → 균등 배치, 제공 시 → 무작위 배치)
    """
    # * Step 1: 입력 검증
    validate_dynamic_inputs(
        num_branches, heads_per_branch,
        branch_spacing_m, head_spacing_m,
        inlet_pressure_mpa, total_flow_lpm,
    )

    total_heads = num_branches * heads_per_branch

    # * Step 2: 비드 배열 초기화
    if bead_heights_2d is None:
        bead_heights_2d = [[0.0] * heads_per_branch for _ in range(num_branches)]

    # * Step 3: 교차배관 구경 자동 선정
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

            junction = HeadJunction(
                index=h,
                pipe_segment=segment,
                bead_height_mm=bead_h,
                K1_welded=K1,
                K2_head=K2_val,
                head_flow_lpm=head_flow,
            )
            junctions.append(junction)

        # * Step 7: 직관 구간 용접 비드 생성
        if weld_beads_2d is not None:
            branch_beads = weld_beads_2d[b]
        elif beads_per_branch > 0:
            branch_beads = generate_branch_beads(
                heads_per_branch, head_spacing_m, beads_per_branch,
                bead_height_for_weld_mm, pipe_sizes, K1_base, rng=rng,
            )
        else:
            branch_beads = []

        bp = BranchPipe(
            branch_index=b,
            num_heads=heads_per_branch,
            junctions=junctions,
            branch_flow_lpm=branch_flow,
            pipe_sizes=pipe_sizes,
            weld_beads=branch_beads,
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
        beads_per_branch=beads_per_branch,
    )


# ══════════════════════════════════════════════
#  PART 5: 동적 시스템 압력 계산
# ══════════════════════════════════════════════

def _calculate_branch_profile(
    branch: BranchPipe,
    branch_inlet_pressure_mpa: float,
    K3_val: float = K3,
) -> dict:
    """
    단일 가지배관의 압력 프로파일 계산 (내부 함수)

    branch_inlet_pressure_mpa: 교차배관 분기점에서의 압력 (교차배관 손실 반영 후)
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

    # * K3 분기 입구 손실
    first_seg = branch.junctions[0].pipe_segment
    V_inlet = velocity_from_flow(total_flow, first_seg.inner_diameter_m)
    K3_loss = head_to_mpa(minor_loss(K3_val, V_inlet))
    current_p -= K3_loss
    current_loss += K3_loss

    for i, junc in enumerate(branch.junctions):
        seg = junc.pipe_segment
        segment_flow = total_flow - (i * head_flow)
        V = velocity_from_flow(segment_flow, seg.inner_diameter_m)
        Re = reynolds_number(V, seg.inner_diameter_m)
        f = friction_factor(Re, D=seg.inner_diameter_m)

        p_major = head_to_mpa(major_loss(f, seg.length_m, seg.inner_diameter_m, V))
        p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V))
        p_K2 = head_to_mpa(minor_loss(junc.K2_head, V))

        # * 직관 구간 내 용접 비드 국부 손실 합산
        beads_in_seg = [b for b in branch.weld_beads if b.segment_index == i]
        p_weld_beads = sum(
            head_to_mpa(minor_loss(b.K_value, V)) for b in beads_in_seg
        )
        n_beads_in_seg = len(beads_in_seg)

        total_seg_loss = p_major + p_K1 + p_K2 + p_weld_beads
        current_p -= total_seg_loss
        current_loss += total_seg_loss

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
            "weld_beads_in_seg": n_beads_in_seg,
            "weld_bead_loss_mpa": round(p_weld_beads, 6),
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
        "segment_details": seg_details,
    }


def calculate_dynamic_system(
    system: DynamicSystem,
    K3_val: float = K3,
) -> dict:
    """
    ! 전체 동적 시스템 압력 계산

    알고리즘:
    1. 교차배관 구간별 손실 누적 → 각 가지배관 분기점 압력 산출
    2. 각 가지배관별 압력 프로파일 계산
    3. 최악 가지배관 (최저 말단 압력) 식별

    반환:
        branch_inlet_pressures : 각 가지배관 분기점 압력
        branch_profiles        : 각 가지배관의 상세 프로파일
        cross_main_losses      : 교차배관 구간별 손실
        worst_branch_index     : 최저 말단 압력 가지배관 인덱스
        worst_terminal_mpa     : 최저 말단 압력
        all_terminal_pressures : 모든 가지배관의 말단 압력 리스트
    """
    n_branches = system.num_branches
    branch_flow = system.total_flow_lpm / n_branches

    # ── Step 1: 교차배관 손실 계산 ──
    cross_main_id_m = get_inner_diameter_m(system.cross_main_size)
    branch_inlet_pressures = []
    cross_main_losses = []
    cm_cumulative_loss = 0.0
    current_cm_pressure = system.inlet_pressure_mpa

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
        )
        branch_profiles.append(profile)
        all_terminal_pressures.append(profile["terminal_pressure_mpa"])

    # ── Step 3: 최악 가지배관 식별 ──
    worst_idx = int(min(range(n_branches), key=lambda i: all_terminal_pressures[i]))
    worst_terminal = all_terminal_pressures[worst_idx]

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
    beads_per_branch: int = 0,
) -> dict:
    """
    ! 동적 시스템에서 Case A(기존) vs Case B(신기술) 비교

    * Case A: 이음쇠 비드(bead_height_existing) + 직관 용접 비드(beads_per_branch, 균등 배치)
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
    )

    beads_A = [[bead_height_existing] * heads_per_branch for _ in range(num_branches)]
    beads_B = [[bead_height_new] * heads_per_branch for _ in range(num_branches)]

    # * Case A: 기존 기술 — 이음쇠 비드 + 직관 용접 비드 (균등 배치)
    sys_A = generate_dynamic_system(
        bead_heights_2d=beads_A,
        beads_per_branch=beads_per_branch,
        bead_height_for_weld_mm=bead_height_existing,
        rng=None,
        **common,
    )
    # * Case B: 신기술 — 비드 없음
    sys_B = generate_dynamic_system(
        bead_heights_2d=beads_B,
        beads_per_branch=0,
        **common,
    )

    result_A = calculate_dynamic_system(sys_A, K3_val)
    result_B = calculate_dynamic_system(sys_B, K3_val)

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
    beads_per_branch: int = 0,
    relaxation: float = 0.5,
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
            beads_per_branch=beads_per_branch,
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
    )

    beads_A = [[bead_height_existing] * heads_per_branch for _ in range(num_branches)]
    beads_B = [[bead_height_new] * heads_per_branch for _ in range(num_branches)]

    result_A = run_grid_system(
        bead_heights_2d=beads_A,
        beads_per_branch=beads_per_branch,
        bead_height_for_weld_mm=bead_height_existing,
        rng=None,
        relaxation=relaxation,
        **common,
    )
    result_B = run_grid_system(
        bead_heights_2d=beads_B,
        beads_per_branch=0,
        relaxation=relaxation,
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
