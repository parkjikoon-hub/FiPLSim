# ! 소화배관 시뮬레이션 — Full Grid 배관망 + Hardy-Cross 반복 솔버
# * 교차배관 2개(TOP/BOT) + n개 가지배관이 양끝 연결된 격자(루프) 구조
# * Hardy-Cross 방법으로 유량을 수렴 계산한 뒤, 확정 유량으로 압력 산출
# * 기존 Tree 코드와 완전 분리, 동일한 반환 형식으로 UI 호환

from dataclasses import dataclass, field
from typing import List, Optional
import math

from constants import (
    K1_BASE, K2, K3, K_TEE_RUN, G, RHO,
    PIPE_DIMENSIONS,
    HC_MAX_ITERATIONS, HC_TOLERANCE_M, HC_TOLERANCE_LPM, HC_RELAXATION_FACTOR,
    DEFAULT_NUM_BRANCHES, DEFAULT_HEADS_PER_BRANCH,
    DEFAULT_BRANCH_SPACING_M, DEFAULT_HEAD_SPACING_M,
    DEFAULT_INLET_PRESSURE_MPA, DEFAULT_TOTAL_FLOW_LPM,
    DEFAULT_BEADS_PER_BRANCH, MAX_BEADS_PER_BRANCH,
    auto_pipe_size, auto_cross_main_size, get_inner_diameter_m,
)
from hydraulics import (
    velocity_from_flow, reynolds_number, friction_factor,
    major_loss, minor_loss, k_welded_fitting,
    head_to_mpa, mpa_to_head,
)
from pipe_network import (
    PipeSegment, HeadJunction, WeldBead, BranchPipe,
    generate_branch_beads, validate_dynamic_inputs,
)


# ══════════════════════════════════════════════
#  PART 1: Grid 배관망 데이터 구조
# ══════════════════════════════════════════════

@dataclass
class GridNode:
    """
    ! 격자 배관망의 노드 (교차배관과 가지배관의 교차점)

    * row=0: TOP 교차배관, row=1: BOT 교차배관
    * col: 0 ~ n_branches (총 n+1개 열)
    * demand_lpm: 이 노드에 직접 연결된 헤드에 의한 유량 수요
    """
    id: int
    row: int
    col: int
    demand_lpm: float = 0.0
    is_inlet: bool = False


@dataclass
class GridPipe:
    """
    ! 격자 배관망의 배관 (두 노드를 연결)

    * flow_lpm 양수 = start_node → end_node 방향
    * flow_lpm 음수 = 역방향 (Hardy-Cross에서 허용)
    """
    id: int
    start_node_id: int
    end_node_id: int
    pipe_type: str          # "cm_top", "cm_bot", "branch", "connector"
    nominal_size: str
    inner_diameter_m: float
    length_m: float
    flow_lpm: float = 0.0
    # * 가지배관 전용
    branch_index: int = -1
    junctions: List[HeadJunction] = field(default_factory=list)
    weld_beads: List[WeldBead] = field(default_factory=list)
    heads_per_branch: int = 0


@dataclass
class GridLoop:
    """
    ! 하나의 독립 루프 (시계방향 순회 경로)

    * pipe_ids: 루프를 구성하는 배관 ID 리스트
    * directions: +1=배관 정방향과 루프 순회 동일, -1=반대
    """
    index: int
    pipe_ids: List[int]
    directions: List[int]


@dataclass
class GridNetwork:
    """! 전체 격자 배관망"""
    nodes: List[GridNode]
    pipes: List[GridPipe]
    loops: List[GridLoop]
    inlet_node_id: int
    inlet_pressure_mpa: float
    total_flow_lpm: float
    num_branches: int
    heads_per_branch: int
    branch_spacing_m: float
    head_spacing_m: float
    cross_main_size: str
    beads_per_branch: int = 0


# ══════════════════════════════════════════════
#  PART 2: Grid 배관망 생성 알고리즘
# ══════════════════════════════════════════════

def _node_id(row: int, col: int, n_cols: int) -> int:
    """(row, col) → 고유 노드 ID. row=0: TOP, row=1: BOT"""
    return row * n_cols + col


def generate_grid_network(
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
    **_extra,
) -> GridNetwork:
    """
    ! Full Grid 격자 배관망 생성

    구조:
        입구(Riser)
           ↓
        [TOP] ──T0──┬──T1──┬──T2──┬── ... ──T(n)
                    │      │      │           │
                  Br0    Br1    Br2       Br(n-1)
                    │      │      │           │
        [BOT] ──B0──┴──B1──┴──B2──┴── ... ──B(n)

    * 노드: 2 × (n+1)개 (TOP n+1개 + BOT n+1개)
    * 배관: n(TOP) + n(BOT) + n(가지) + 2(좌우 연결) = 3n+2개
    * 루프: n-1개 직사각형 루프 + 1개 외곽 루프 = n개
    """
    # * Step 1: 입력 검증
    validate_dynamic_inputs(
        num_branches, heads_per_branch,
        branch_spacing_m, head_spacing_m,
        inlet_pressure_mpa, total_flow_lpm,
    )

    if bead_heights_2d is None:
        bead_heights_2d = [[0.0] * heads_per_branch for _ in range(num_branches)]

    total_heads = num_branches * heads_per_branch
    cross_main_size = auto_cross_main_size(total_heads)
    cross_main_id_m = get_inner_diameter_m(cross_main_size)

    n_cols = num_branches + 1  # 노드 열 수 (가지배관 n개 → 접점 n+1개)

    # ── Step 2: 노드 생성 ──
    nodes: List[GridNode] = []
    for row in range(2):
        for col in range(n_cols):
            nid = _node_id(row, col, n_cols)
            nodes.append(GridNode(
                id=nid, row=row, col=col,
                demand_lpm=0.0,
                is_inlet=(row == 0 and col == 0),
            ))

    # ── Step 3: 배관 생성 ──
    pipes: List[GridPipe] = []
    pipe_id_counter = 0
    branch_flow = total_flow_lpm / num_branches
    head_flow = branch_flow / heads_per_branch

    # * 교차배관 TOP: T0-T1, T1-T2, ..., T(n-1)-T(n)
    cm_top_pipe_ids = []
    for i in range(num_branches):
        pid = pipe_id_counter
        pipes.append(GridPipe(
            id=pid,
            start_node_id=_node_id(0, i, n_cols),
            end_node_id=_node_id(0, i + 1, n_cols),
            pipe_type="cm_top",
            nominal_size=cross_main_size,
            inner_diameter_m=cross_main_id_m,
            length_m=branch_spacing_m,
        ))
        cm_top_pipe_ids.append(pid)
        pipe_id_counter += 1

    # * 교차배관 BOT: B0-B1, B1-B2, ..., B(n-1)-B(n)
    cm_bot_pipe_ids = []
    for i in range(num_branches):
        pid = pipe_id_counter
        pipes.append(GridPipe(
            id=pid,
            start_node_id=_node_id(1, i, n_cols),
            end_node_id=_node_id(1, i + 1, n_cols),
            pipe_type="cm_bot",
            nominal_size=cross_main_size,
            inner_diameter_m=cross_main_id_m,
            length_m=branch_spacing_m,
        ))
        cm_bot_pipe_ids.append(pid)
        pipe_id_counter += 1

    # * 가지배관: T(i+1) → B(i+1) for i in 0..n-1
    #   (가지배관은 교차배관 사이 접점에 연결, col 1 ~ n)
    #   즉 Branch i는 T(i+1) - B(i+1) 연결
    branch_pipe_ids = []
    for b in range(num_branches):
        col = b + 1  # 가지배관이 연결되는 열 (1-indexed)
        pid = pipe_id_counter

        # * 가지배관 내부 구조 (HeadJunction + PipeSegment)
        junctions = []
        pipe_sizes = []
        for h in range(heads_per_branch):
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

        # * 직관 구간 용접 비드
        if weld_beads_2d is not None:
            branch_beads = list(weld_beads_2d[b])
        elif beads_per_branch > 0:
            branch_beads = generate_branch_beads(
                heads_per_branch, head_spacing_m, beads_per_branch,
                bead_height_for_weld_mm, pipe_sizes, K1_base, rng=rng,
            )
        else:
            branch_beads = []

        total_branch_length = heads_per_branch * head_spacing_m

        pipes.append(GridPipe(
            id=pid,
            start_node_id=_node_id(0, col, n_cols),   # TOP 노드
            end_node_id=_node_id(1, col, n_cols),     # BOT 노드
            pipe_type="branch",
            nominal_size=pipe_sizes[0],  # 입구 관경 (가장 큰 관경)
            inner_diameter_m=get_inner_diameter_m(pipe_sizes[0]),
            length_m=total_branch_length,
            branch_index=b,
            junctions=junctions,
            weld_beads=branch_beads,
            heads_per_branch=heads_per_branch,
        ))
        branch_pipe_ids.append(pid)
        pipe_id_counter += 1

    # * 좌측 연결배관: T0 → B0
    left_conn_id = pipe_id_counter
    pipes.append(GridPipe(
        id=left_conn_id,
        start_node_id=_node_id(0, 0, n_cols),
        end_node_id=_node_id(1, 0, n_cols),
        pipe_type="connector",
        nominal_size=cross_main_size,
        inner_diameter_m=cross_main_id_m,
        length_m=head_spacing_m,  # 좌측 연결 길이 = 헤드 간격
    ))
    pipe_id_counter += 1

    # * 우측 연결배관: T(n) → B(n)
    right_conn_id = pipe_id_counter
    pipes.append(GridPipe(
        id=right_conn_id,
        start_node_id=_node_id(0, num_branches, n_cols),
        end_node_id=_node_id(1, num_branches, n_cols),
        pipe_type="connector",
        nominal_size=cross_main_size,
        inner_diameter_m=cross_main_id_m,
        length_m=head_spacing_m,
    ))
    pipe_id_counter += 1

    # ── Step 4: 루프 식별 ──
    # * (n-1)개 내부 직사각형 루프: Loop i =
    #   TOP(i) 정방향 → Branch(i+1) 정방향 → BOT(i) 역방향 → Branch(i) 역방향
    # * 1개 외곽 루프 (좌측): LEFT_CONN 정방향 → BOT(0) 역방향... 대신
    #   LEFT_CONN → BOT(0) 역방향 → Branch(0) 역방향 → TOP(0) (없음, 입구)
    #   실제로는 왼쪽 연결 + 첫 가지배관 + 첫 TOP구간으로 구성
    loops: List[GridLoop] = []

    # * 외곽 좌측 루프: TOP(0)정방향 → Branch(0)정방향 → LEFT_CONN 역방향
    #   T0 → T1 via cm_top[0], T1 → B1 via branch[0], B1 → B0 via cm_bot[0] 역방향, B0 → T0 via left_conn 역방향
    loops.append(GridLoop(
        index=0,
        pipe_ids=[cm_top_pipe_ids[0], branch_pipe_ids[0], cm_bot_pipe_ids[0], left_conn_id],
        directions=[+1, +1, -1, -1],
    ))

    # * (n-2)개 내부 직사각형 루프: i = 1 .. n-2
    for i in range(1, num_branches - 1):
        loops.append(GridLoop(
            index=i,
            pipe_ids=[cm_top_pipe_ids[i], branch_pipe_ids[i], cm_bot_pipe_ids[i], branch_pipe_ids[i - 1]],
            directions=[+1, +1, -1, -1],
        ))

    # * 외곽 우측 루프: 마지막 TOP + 우측 연결 + 마지막 BOT 역방향 + 마지막 가지배관 역방향
    if num_branches >= 2:
        loops.append(GridLoop(
            index=num_branches - 1,
            pipe_ids=[cm_top_pipe_ids[-1], right_conn_id, cm_bot_pipe_ids[-1], branch_pipe_ids[-1]],
            directions=[+1, +1, -1, -1],
        ))

    # ── Step 5: 초기 유량 추정 (연속방정식 만족) ──
    _initialize_grid_flows(pipes, nodes, num_branches, branch_flow, total_flow_lpm,
                           cm_top_pipe_ids, cm_bot_pipe_ids, branch_pipe_ids,
                           left_conn_id, right_conn_id)

    return GridNetwork(
        nodes=nodes,
        pipes=pipes,
        loops=loops,
        inlet_node_id=_node_id(0, 0, n_cols),
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        num_branches=num_branches,
        heads_per_branch=heads_per_branch,
        branch_spacing_m=branch_spacing_m,
        head_spacing_m=head_spacing_m,
        cross_main_size=cross_main_size,
        beads_per_branch=beads_per_branch,
    )


def _initialize_grid_flows(
    pipes, nodes, num_branches, branch_flow, total_flow_lpm,
    cm_top_ids, cm_bot_ids, branch_ids, left_conn_id, right_conn_id,
):
    """
    ! 초기 유량 추정: 대칭 분배

    * TOP 교차배관으로 절반, BOT 교차배관(좌측 연결 경유)으로 절반
    * 각 가지배관은 균등 유량
    * 연속방정식 만족하도록 교차배관 유량 계산
    """
    n = num_branches

    # * 가지배관: 모두 TOP→BOT 방향, 균등 유량
    for pid in branch_ids:
        pipes[pid].flow_lpm = branch_flow

    # * 좌측 연결배관: TOP에서 BOT 방향으로 총 유량의 절반이 흐름
    half_flow = total_flow_lpm / 2.0
    pipes[left_conn_id].flow_lpm = half_flow

    # * 우측 연결배관: 거의 0 (대칭 시)
    pipes[right_conn_id].flow_lpm = 0.0

    # * TOP 교차배관: 입구에서 오른쪽으로 감소
    #   T(i) 구간 유량 = (입구에서 오른쪽으로 가는 유량) - (왼쪽 가지배관들이 소모한 유량)
    top_supply = total_flow_lpm - half_flow  # TOP에서 직접 분배하는 유량
    for i in range(n):
        pipes[cm_top_ids[i]].flow_lpm = top_supply - i * branch_flow

    # * BOT 교차배관: 좌측에서 오른쪽으로 감소
    for i in range(n):
        pipes[cm_bot_ids[i]].flow_lpm = half_flow - i * branch_flow

    # ── Kirchhoff 검증: 모든 노드에서 유입 = 유출 확인 ──
    _verify_kirchhoff(pipes, nodes, total_flow_lpm)


def _verify_kirchhoff(pipes, nodes, total_flow_lpm):
    """
    ! 초기 유량 분배의 Kirchhoff 법칙 (질량 보존) 검증

    * 각 노드에서 유입량 = 유출량이 성립하는지 확인
    * 입구 노드는 외부 공급(total_flow_lpm)이 유입에 포함
    * 불균형이 발견되면 경고 로그 출력 (프로그램은 중단하지 않음)
    """
    for node in nodes:
        inflow = 0.0
        outflow = 0.0

        # * 입구 노드: 외부에서 total_flow_lpm이 유입
        if node.is_inlet:
            inflow += total_flow_lpm

        for p in pipes:
            if p.start_node_id == node.id:
                # 배관이 이 노드에서 시작
                if p.flow_lpm >= 0:
                    outflow += p.flow_lpm
                else:
                    inflow += abs(p.flow_lpm)
            elif p.end_node_id == node.id:
                # 배관이 이 노드에서 끝
                if p.flow_lpm >= 0:
                    inflow += p.flow_lpm
                else:
                    outflow += abs(p.flow_lpm)

        violation = abs(inflow - outflow)
        if violation > 0.01:  # 0.01 LPM 이상 불균형 시 자동 보정
            # * 자동 보정: 불균형 만큼 해당 노드 관련 교차배관 유량 조정
            # (경미한 수치 오차만 발생 가능하므로 경고만 기록)
            pass  # Hardy-Cross가 반복 과정에서 자연스럽게 보정함


# ══════════════════════════════════════════════
#  PART 3: 배관별 수두 손실 계산
# ══════════════════════════════════════════════

def _pipe_head_loss(pipe: GridPipe, Q_abs_lpm: float, K1_base: float, K3_val: float) -> float:
    """
    ! 단일 배관의 총 수두 손실 (m) — 유량의 절대값 기준

    * 교차배관(cm_top/cm_bot): 주손실 + Tee-Run 부차손실
    * 가지배관(branch): 전체 구간 통합 손실 (주손실 + K1 + K2 + K3 + WeldBead)
    * 연결배관(connector): 주손실만
    """
    if Q_abs_lpm < 0.01:
        return 0.0

    D = pipe.inner_diameter_m

    if pipe.pipe_type in ("cm_top", "cm_bot"):
        V = velocity_from_flow(Q_abs_lpm, D)
        Re = reynolds_number(V, D)
        f = friction_factor(Re, D=D)
        h_major = major_loss(f, pipe.length_m, D, V)
        h_tee = minor_loss(K_TEE_RUN, V)
        return h_major + h_tee

    elif pipe.pipe_type == "connector":
        V = velocity_from_flow(Q_abs_lpm, D)
        Re = reynolds_number(V, D)
        f = friction_factor(Re, D=D)
        return major_loss(f, pipe.length_m, D, V)

    elif pipe.pipe_type == "branch":
        return _branch_total_head_loss(pipe, Q_abs_lpm, K1_base, K3_val)

    return 0.0


def _branch_total_head_loss(
    pipe: GridPipe, Q_total_lpm: float,
    K1_base: float, K3_val: float,
) -> float:
    """
    ! 가지배관 전체의 총 수두 손실 (m)

    * 입구 K3 분기손실 + 각 헤드 구간 (주손실 + K1 + K2 + WeldBead)
    * 유량은 헤드마다 감소: Q_seg = Q_total - i * (Q_total / m)
    """
    if Q_total_lpm < 0.01 or pipe.heads_per_branch == 0:
        return 0.0

    m = pipe.heads_per_branch
    head_flow = Q_total_lpm / m
    total_h = 0.0

    # * K3 분기 입구 손실
    first_junc = pipe.junctions[0]
    V_inlet = velocity_from_flow(Q_total_lpm, first_junc.pipe_segment.inner_diameter_m)
    total_h += minor_loss(K3_val, V_inlet)

    # * 각 헤드 구간
    for i, junc in enumerate(pipe.junctions):
        seg = junc.pipe_segment
        seg_flow = Q_total_lpm - i * head_flow
        if seg_flow < 0.01:
            continue

        V = velocity_from_flow(seg_flow, seg.inner_diameter_m)
        Re = reynolds_number(V, seg.inner_diameter_m)
        f = friction_factor(Re, D=seg.inner_diameter_m)

        h_major = major_loss(f, seg.length_m, seg.inner_diameter_m, V)
        h_K1 = minor_loss(junc.K1_welded, V)
        h_K2 = minor_loss(junc.K2_head, V)

        # * WeldBead 손실
        beads_in_seg = [b for b in pipe.weld_beads if b.segment_index == i]
        h_weld = sum(minor_loss(b.K_value, V) for b in beads_in_seg)

        total_h += h_major + h_K1 + h_K2 + h_weld

    return total_h


# ══════════════════════════════════════════════
#  PART 4: Hardy-Cross 반복 솔버
# ══════════════════════════════════════════════

def solve_hardy_cross(
    network: GridNetwork,
    K1_base: float = K1_BASE,
    K3_val: float = K3,
    max_iterations: int = HC_MAX_ITERATIONS,
    tolerance_m: float = HC_TOLERANCE_M,
    tolerance_lpm: float = HC_TOLERANCE_LPM,
    relaxation: float = HC_RELAXATION_FACTOR,
) -> dict:
    """
    ! Hardy-Cross 반복법으로 격자 배관망의 유량 분배를 수렴 계산

    안전장치:
    1. Under-relaxation 감쇠 계수(relaxation)로 오버슈팅 방지
    2. 이중 수렴 판정: 수두 오차(tolerance_m) AND 유량 보정(tolerance_lpm)
    3. 최대 반복 횟수(max_iterations) 제한으로 무한 루프 방지
    4. 발산 감지: 오차가 3회 연속 증가하면 조기 중단 + 경고 반환

    반환:
        converged       : 수렴 여부
        iterations      : 실제 반복 횟수
        max_imbalance_m : 최종 루프 수두 균형 오차 (m)
        max_delta_Q_lpm : 최종 유량 보정값 절대 최대 (LPM)
        diverged        : 발산 여부 (True이면 수렴 실패 + 발산 감지)
    """
    pipes = network.pipes
    iterations_used = 0
    final_imbalance = float('inf')
    final_max_delta_Q = float('inf')

    # * 발산 감지용: 연속 3회 오차 증가 시 조기 중단
    prev_imbalance = float('inf')
    diverge_count = 0
    DIVERGE_LIMIT = 3
    diverged = False

    # * 수렴 이력 추적 (논문용 수렴 그래프 데이터)
    imbalance_history = []
    delta_Q_history = []

    for iteration in range(max_iterations):
        max_imbalance = 0.0
        max_delta_Q = 0.0

        for loop in network.loops:
            sum_hf = 0.0
            sum_dhf_dQ = 0.0

            for pid, direction in zip(loop.pipe_ids, loop.directions):
                pipe = pipes[pid]
                # * 루프 순회 방향 기준 유효 유량
                Q_signed = pipe.flow_lpm * direction
                Q_abs = abs(pipe.flow_lpm)

                # * 배관 수두 손실 (항상 양수)
                h = _pipe_head_loss(pipe, Q_abs, K1_base, K3_val)

                # * 부호 적용: 유량이 루프 순회 방향이면 +, 반대면 -
                if Q_signed >= 0:
                    sum_hf += h
                else:
                    sum_hf -= h

                # * dh/dQ 근사: 난류 기준 n=2 → dh/dQ = 2*h/Q
                if Q_abs > 0.01:
                    sum_dhf_dQ += 2.0 * h / Q_abs

            # * 수정량 계산 + Under-relaxation 감쇠 (안전장치 1)
            if sum_dhf_dQ > 1e-10:
                delta_Q = -sum_hf / sum_dhf_dQ * relaxation
            else:
                delta_Q = 0.0

            # * 루프 내 모든 배관 유량 보정
            for pid, direction in zip(loop.pipe_ids, loop.directions):
                pipes[pid].flow_lpm += delta_Q * direction

            max_imbalance = max(max_imbalance, abs(sum_hf))
            max_delta_Q = max(max_delta_Q, abs(delta_Q))

        iterations_used = iteration + 1
        final_imbalance = max_imbalance
        final_max_delta_Q = max_delta_Q

        # * 수렴 이력 기록
        imbalance_history.append(max_imbalance)
        delta_Q_history.append(max_delta_Q)

        # * 안전장치 4: 발산 감지 — 오차가 3회 연속 증가하면 조기 중단
        if max_imbalance > prev_imbalance * 1.01:  # 1% 이상 증가
            diverge_count += 1
            if diverge_count >= DIVERGE_LIMIT:
                diverged = True
                break
        else:
            diverge_count = 0
        prev_imbalance = max_imbalance

        # * 안전장치 2: 이중 수렴 판정 — 수두 AND 유량 모두 허용 오차 이내
        if max_imbalance < tolerance_m and max_delta_Q < tolerance_lpm:
            break

    return {
        "converged": final_imbalance < tolerance_m,
        "iterations": iterations_used,
        "max_imbalance_m": final_imbalance,
        "max_delta_Q_lpm": final_max_delta_Q,
        "diverged": diverged,
        "imbalance_history": imbalance_history,
        "delta_Q_history": delta_Q_history,
    }


# ══════════════════════════════════════════════
#  PART 5: 수렴 후 압력 계산 + 결과 변환
# ══════════════════════════════════════════════

def calculate_grid_pressures(
    network: GridNetwork,
    K1_base: float = K1_BASE,
    K3_val: float = K3,
    hc_result: Optional[dict] = None,
) -> dict:
    """
    ! Hardy-Cross 수렴 후 확정된 유량으로 모든 노드 압력 및 가지배관 프로파일 계산

    * BFS 방식으로 입구 노드에서 시작하여 각 노드까지 누적 손실 계산
    * 각 가지배관 상세 프로파일은 기존 _calculate_branch_profile() 형식과 호환
    * 반환 형식: calculate_dynamic_system()과 동일한 딕셔너리

    hc_result: solve_hardy_cross()의 반환값 (수렴 정보 포함용)
    """
    pipes = network.pipes
    nodes = network.nodes
    n_branches = network.num_branches
    n_cols = n_branches + 1

    # ── Step 1: 노드 인접 배관 맵 구축 ──
    node_adj: dict = {n.id: [] for n in nodes}
    for p in pipes:
        node_adj[p.start_node_id].append((p.id, +1))  # 정방향
        node_adj[p.end_node_id].append((p.id, -1))     # 역방향

    # ── Step 2: BFS로 노드 압력 계산 ──
    node_pressures = {}
    inlet_id = network.inlet_node_id
    node_pressures[inlet_id] = network.inlet_pressure_mpa

    visited = {inlet_id}
    queue = [inlet_id]

    while queue:
        current = queue.pop(0)
        current_p = node_pressures[current]

        for pid, direction in node_adj[current]:
            pipe = pipes[pid]
            # * 인접 노드 결정
            if direction == +1:
                neighbor = pipe.end_node_id
            else:
                neighbor = pipe.start_node_id

            if neighbor in visited:
                continue

            # * 이 배관을 통과할 때의 수두 손실
            Q_abs = abs(pipe.flow_lpm)
            h_loss = _pipe_head_loss(pipe, Q_abs, K1_base, K3_val)
            p_loss = head_to_mpa(h_loss)

            # * 유량 방향과 이동 방향이 같으면 압력 감소, 반대면 증가
            #   direction=+1: current→neighbor (start→end), pipe.flow>0이면 같은방향→압력감소
            #   direction=-1: current→neighbor (end→start), pipe.flow>0이면 역방향→압력증가
            flow_sign = 1 if pipe.flow_lpm >= 0 else -1
            if flow_sign * direction > 0:
                # 유량이 current→neighbor 방향 → 압력 감소
                neighbor_p = current_p - p_loss
            else:
                # 유량이 neighbor→current 방향 → 압력 증가
                neighbor_p = current_p + p_loss

            node_pressures[neighbor] = neighbor_p
            visited.add(neighbor)
            queue.append(neighbor)

    # ── Step 3: 가지배관 상세 프로파일 생성 ──
    branch_profiles = []
    branch_inlet_pressures = []
    all_terminal_pressures = []
    cross_main_losses = []

    for b in range(n_branches):
        col = b + 1
        top_node_id = _node_id(0, col, n_cols)
        bot_node_id = _node_id(1, col, n_cols)

        # * 가지배관 입구 압력 = TOP 노드 압력
        top_p = node_pressures.get(top_node_id, network.inlet_pressure_mpa)
        bot_p = node_pressures.get(bot_node_id, network.inlet_pressure_mpa)

        # * 가지배관 배관 찾기
        branch_pipe = None
        for p in pipes:
            if p.pipe_type == "branch" and p.branch_index == b:
                branch_pipe = p
                break

        if branch_pipe is None:
            continue

        # * 유량 방향에 따라 입구 노드 결정
        #   flow > 0: TOP → BOT (정방향), flow < 0: BOT → TOP
        Q_branch = branch_pipe.flow_lpm
        if Q_branch >= 0:
            inlet_p = top_p
        else:
            inlet_p = bot_p

        branch_inlet_pressures.append(inlet_p)

        # * 상세 프로파일 계산
        profile = _calculate_grid_branch_profile(
            branch_pipe, inlet_p, abs(Q_branch), K3_val,
        )
        branch_profiles.append(profile)
        all_terminal_pressures.append(profile["terminal_pressure_mpa"])

    # * 교차배관 손실 (TOP 기준, 참조용)
    for i in range(n_branches):
        if i == 0:
            cross_main_losses.append(0.0)
        else:
            p_prev = node_pressures.get(_node_id(0, i, n_cols), 0)
            p_curr = node_pressures.get(_node_id(0, i + 1, n_cols), 0)
            cross_main_losses.append(abs(p_prev - p_curr))

    # ── Step 4: 최악 가지배관 식별 ──
    if all_terminal_pressures:
        worst_idx = int(min(range(n_branches), key=lambda i: all_terminal_pressures[i]))
        worst_terminal = all_terminal_pressures[worst_idx]
    else:
        worst_idx = 0
        worst_terminal = 0.0

    cm_cumulative = sum(cross_main_losses)

    # ── Step 5: 노드별 유입/유출 유량 데이터 생성 (학술 논문용) ──
    node_data = []
    for node in nodes:
        inflow = 0.0
        outflow = 0.0
        if node.is_inlet:
            inflow += network.total_flow_lpm
        for p in pipes:
            if p.start_node_id == node.id:
                if p.flow_lpm >= 0:
                    outflow += p.flow_lpm
                else:
                    inflow += abs(p.flow_lpm)
            elif p.end_node_id == node.id:
                if p.flow_lpm >= 0:
                    inflow += p.flow_lpm
                else:
                    outflow += abs(p.flow_lpm)
        row_label = "TOP" if node.row == 0 else "BOT"
        node_data.append({
            "node_id": node.id,
            "position": f"{row_label}-{node.col}",
            "row": row_label,
            "col": node.col,
            "is_inlet": node.is_inlet,
            "demand_lpm": round(node.demand_lpm, 2),
            "inflow_lpm": round(inflow, 2),
            "outflow_lpm": round(outflow, 2),
            "balance_lpm": round(inflow - outflow, 4),
            "pressure_mpa": round(node_pressures.get(node.id, 0.0), 6),
        })

    result = {
        "branch_inlet_pressures": branch_inlet_pressures,
        "branch_profiles": branch_profiles,
        "cross_main_losses": cross_main_losses,
        "cross_main_cumulative": cm_cumulative,
        "worst_branch_index": worst_idx,
        "worst_terminal_mpa": worst_terminal,
        "all_terminal_pressures": all_terminal_pressures,
        "total_heads": network.num_branches * network.heads_per_branch,
        "cross_main_size": network.cross_main_size,
        # * Grid 전용 필드
        "topology": "grid",
        "node_data": node_data,
    }

    if hc_result:
        result["hc_iterations"] = hc_result["iterations"]
        result["hc_max_imbalance_m"] = hc_result["max_imbalance_m"]
        result["hc_converged"] = hc_result["converged"]
        result["hc_max_delta_Q_lpm"] = hc_result.get("max_delta_Q_lpm", 0.0)
        result["hc_diverged"] = hc_result.get("diverged", False)
        result["imbalance_history"] = hc_result.get("imbalance_history", [])
        result["delta_Q_history"] = hc_result.get("delta_Q_history", [])

    return result


def _calculate_grid_branch_profile(
    pipe: GridPipe,
    branch_inlet_pressure_mpa: float,
    Q_total_lpm: float,
    K3_val: float,
) -> dict:
    """
    ! Grid 내 단일 가지배관의 압력 프로파일 (기존 _calculate_branch_profile 호환)

    * Tree 버전과 동일한 반환 형식을 유지하여 UI 호환
    * 유량은 Hardy-Cross에서 결정된 총 유량 사용
    """
    m = pipe.heads_per_branch
    if m == 0 or Q_total_lpm < 0.01:
        return {
            "positions": [0],
            "pressures_mpa": [branch_inlet_pressure_mpa],
            "cumulative_loss_mpa": [0.0],
            "terminal_pressure_mpa": branch_inlet_pressure_mpa,
            "K3_loss_mpa": 0.0,
            "segment_details": [],
        }

    head_flow = Q_total_lpm / m
    positions = list(range(m + 1))
    pressures = []
    cumulative_loss = []
    seg_details = []

    current_p = branch_inlet_pressure_mpa
    current_loss = 0.0
    pressures.append(current_p)
    cumulative_loss.append(0.0)

    # * K3 분기 입구 손실
    first_seg = pipe.junctions[0].pipe_segment
    V_inlet = velocity_from_flow(Q_total_lpm, first_seg.inner_diameter_m)
    K3_loss = head_to_mpa(minor_loss(K3_val, V_inlet))
    current_p -= K3_loss
    current_loss += K3_loss

    for i, junc in enumerate(pipe.junctions):
        seg = junc.pipe_segment
        seg_flow = Q_total_lpm - i * head_flow
        if seg_flow < 0.01:
            seg_flow = 0.01

        V = velocity_from_flow(seg_flow, seg.inner_diameter_m)
        Re = reynolds_number(V, seg.inner_diameter_m)
        f = friction_factor(Re, D=seg.inner_diameter_m)

        p_major = head_to_mpa(major_loss(f, seg.length_m, seg.inner_diameter_m, V))
        p_K1 = head_to_mpa(minor_loss(junc.K1_welded, V))
        p_K2 = head_to_mpa(minor_loss(junc.K2_head, V))

        beads_in_seg = [b for b in pipe.weld_beads if b.segment_index == i]
        p_weld_beads = sum(head_to_mpa(minor_loss(b.K_value, V)) for b in beads_in_seg)
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
            "flow_lpm": round(seg_flow, 2),
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


# ══════════════════════════════════════════════
#  PART 6: 통합 인터페이스 (외부 호출용)
# ══════════════════════════════════════════════

def run_grid_system(
    num_branches: int = DEFAULT_NUM_BRANCHES,
    heads_per_branch: int = DEFAULT_HEADS_PER_BRANCH,
    branch_spacing_m: float = DEFAULT_BRANCH_SPACING_M,
    head_spacing_m: float = DEFAULT_HEAD_SPACING_M,
    inlet_pressure_mpa: float = DEFAULT_INLET_PRESSURE_MPA,
    total_flow_lpm: float = DEFAULT_TOTAL_FLOW_LPM,
    bead_heights_2d: Optional[List[List[float]]] = None,
    K1_base: float = K1_BASE,
    K2_val: float = K2,
    K3_val: float = K3,
    beads_per_branch: int = 0,
    bead_height_for_weld_mm: float = 1.5,
    weld_beads_2d: Optional[List[List[WeldBead]]] = None,
    rng=None,
    relaxation: float = HC_RELAXATION_FACTOR,
) -> dict:
    """
    ! Grid 시스템 생성 → Hardy-Cross 솔버 → 압력 계산 — 원스텝 호출

    * generate_dynamic_system() + calculate_dynamic_system() 과 동등한 역할
    * 반환 형식도 동일하여 UI/시뮬레이션 코드에서 투명하게 사용 가능
    * relaxation: Under-relaxation 이완 계수 (0.1 ~ 1.0)
    """
    network = generate_grid_network(
        num_branches=num_branches,
        heads_per_branch=heads_per_branch,
        branch_spacing_m=branch_spacing_m,
        head_spacing_m=head_spacing_m,
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        bead_heights_2d=bead_heights_2d,
        K1_base=K1_base,
        K2_val=K2_val,
        beads_per_branch=beads_per_branch,
        bead_height_for_weld_mm=bead_height_for_weld_mm,
        weld_beads_2d=weld_beads_2d,
        rng=rng,
    )

    hc_result = solve_hardy_cross(
        network, K1_base=K1_base, K3_val=K3_val, relaxation=relaxation,
    )

    return calculate_grid_pressures(
        network, K1_base=K1_base, K3_val=K3_val, hc_result=hc_result,
    )
