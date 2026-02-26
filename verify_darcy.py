"""
FiPLSim 검증 스크립트: 비드 0mm(Case B) 시뮬레이션 vs Darcy-Weisbach 수계산 직접 비교

목적: FiPLSim의 수치해석 엔진이 Darcy-Weisbach 이론 해석값과
      정확히 일치하는지 구간별로 검증합니다.

조건: 4 가지배관 × 8 헤드, 비드 0mm, beads_per_branch=0
      입구 압력 1.4 MPa, 설계 유량 400 LPM
"""

import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ════════════════════════════════════════════
#  Part 1: 이론 수계산 (순수 수학, FiPLSim 코드 미사용)
# ════════════════════════════════════════════

# --- 물성치 ---
RHO = 998.0          # kg/m³
G = 9.81             # m/s²
NU = 1.002e-3 / RHO  # m²/s (≈ 1.004e-6)
EPSILON = 4.5e-5     # m (0.045 mm)

# --- K-factor ---
K1_BASE = 0.5   # 비드 0mm → K1 = 0.5
K2 = 2.5
K3 = 1.0
K_TEE_RUN = 0.3

# --- NFSC 103 배관 치수 (JIS/KS Sch 40) ---
PIPES = {
    "25A": 26.64e-3,   # m
    "32A": 35.04e-3,
    "40A": 40.90e-3,
    "50A": 52.51e-3,
    "65A": 62.71e-3,
    "80A": 77.92e-3,
}

# --- 자동 관경 선정 ---
def pipe_size(n_downstream):
    if n_downstream >= 12: return "65A"
    if n_downstream >= 6:  return "50A"
    if n_downstream >= 4:  return "40A"
    if n_downstream >= 3:  return "32A"
    return "25A"

# --- Colebrook-White 마찰계수 ---
def calc_friction(Re, D):
    if Re < 2300:
        return 64.0 / Re
    A = (EPSILON / D) / 3.7
    B = 2.51 / Re
    f = 0.25 / (math.log10(A + B / math.sqrt(0.02))) ** 2
    for _ in range(10):
        sf = math.sqrt(f)
        inner = A + B / sf
        if inner <= 0: inner = 1e-10
        rhs = -2.0 * math.log10(inner)
        f_new = 1.0 / rhs ** 2
        if abs(f_new - f) / max(f, 1e-12) < 1e-8:
            return f_new
        f = f_new
    return f

# --- 유속 계산 ---
def calc_velocity(Q_lpm, D_m):
    Q = Q_lpm / 60000.0
    A = math.pi * (D_m / 2.0) ** 2
    return Q / A

# --- Darcy-Weisbach 주손실 ---
def calc_major_loss(f, L, D, V):
    return f * (L / D) * (V ** 2 / (2.0 * G))

# --- K-factor 부차손실 ---
def calc_minor_loss(K, V):
    return K * (V ** 2 / (2.0 * G))

# --- 수두→MPa ---
def head_to_mpa(h):
    return RHO * G * h / 1e6


# ════════════════════════════════════════════
#  Part 2: 이론 수계산 실행
# ════════════════════════════════════════════

def run_hand_calc():
    """순수 수계산으로 최악 가지배관 말단 압력 산출"""
    n_branches = 4
    n_heads = 8
    branch_spacing = 3.5
    head_spacing = 2.3
    inlet_P = 1.4  # MPa
    total_flow = 400.0  # LPM

    branch_flow = total_flow / n_branches  # 100 LPM
    head_flow = branch_flow / n_heads      # 12.5 LPM

    # 교차배관: 32 heads → 80A
    cm_D = PIPES["80A"]

    print("=" * 70)
    print("  PART 1: Darcy-Weisbach 이론 수계산 (비드 0mm, beads=0)")
    print("=" * 70)
    print(f"  입구 압력: {inlet_P} MPa | 유량: {total_flow} LPM")
    print(f"  구성: {n_branches} 가지배관 × {n_heads} 헤드 = {n_branches * n_heads} 헤드")
    print(f"  교차배관: 80A (ID={cm_D*1000:.2f} mm)")
    print(f"  가지배관 유량: {branch_flow} LPM | 헤드 유량: {head_flow} LPM")
    print()

    # --- 교차배관 손실 (최악 = B#4, 가장 먼 가지배관) ---
    print("  [교차배관 손실 — B#4 분기점까지]")
    cm_total_loss = 0.0
    for i in range(1, n_branches):
        remaining = total_flow - i * branch_flow
        V = calc_velocity(remaining, cm_D)
        Re = V * cm_D / NU
        f = calc_friction(Re, cm_D)
        h_major = calc_major_loss(f, branch_spacing, cm_D, V)
        h_tee = calc_minor_loss(K_TEE_RUN, V)
        seg_loss = head_to_mpa(h_major + h_tee)
        cm_total_loss += seg_loss
        print(f"    구간 {i}: 잔여유량={remaining:.1f} LPM, V={V:.4f} m/s, "
              f"Re={Re:.0f}, f={f:.6f}, 손실={seg_loss:.6f} MPa")

    branch_inlet = inlet_P - cm_total_loss
    print(f"  교차배관 총 손실: {cm_total_loss:.6f} MPa")
    print(f"  B#4 분기점 압력: {branch_inlet:.6f} MPa")
    print()

    # --- K3 분기 입구 손실 ---
    first_pipe_size = pipe_size(n_heads)
    first_D = PIPES[first_pipe_size]
    V_inlet = calc_velocity(branch_flow, first_D)
    K3_loss = head_to_mpa(calc_minor_loss(K3, V_inlet))
    current_P = branch_inlet - K3_loss
    print(f"  [K3 분기 입구 손실] K3={K3}, V={V_inlet:.4f}, 손실={K3_loss:.6f} MPa")
    print(f"  K3 후 압력: {current_P:.6f} MPa")
    print()

    # --- 구간별 압력 프로파일 ---
    print("  [가지배관 B#4 구간별 압력 프로파일]")
    print(f"  {'구간':>4} {'관경':>5} {'유량':>10} {'유속':>10} {'Re':>10} "
          f"{'f':>10} {'주손실':>12} {'K1손실':>12} {'K2손실':>12} "
          f"{'총손실':>12} {'잔여압력':>12}")
    print("  " + "-" * 120)

    seg_details = []
    for h in range(n_heads):
        downstream = n_heads - h
        p_size = pipe_size(downstream)
        D = PIPES[p_size]
        seg_flow = branch_flow - h * head_flow
        V = calc_velocity(seg_flow, D)
        Re = V * D / NU
        f = calc_friction(Re, D)

        h_major = head_to_mpa(calc_major_loss(f, head_spacing, D, V))
        h_K1 = head_to_mpa(calc_minor_loss(K1_BASE, V))  # bead=0 → K1=0.5
        h_K2 = head_to_mpa(calc_minor_loss(K2, V))
        total_loss = h_major + h_K1 + h_K2

        current_P -= total_loss
        seg_details.append({
            "head": h + 1, "pipe": p_size, "flow": seg_flow,
            "V": V, "Re": Re, "f": f,
            "major": h_major, "K1": h_K1, "K2": h_K2,
            "total": total_loss, "P": current_P,
        })
        print(f"  {h+1:>4} {p_size:>5} {seg_flow:>10.2f} {V:>10.4f} {Re:>10.0f} "
              f"{f:>10.6f} {h_major:>12.6f} {h_K1:>12.6f} {h_K2:>12.6f} "
              f"{total_loss:>12.6f} {current_P:>12.6f}")

    print()
    print(f"  >> 이론 수계산 말단 압력 (B#4, Head #8): {current_P:.6f} MPa")
    return current_P, seg_details


# ════════════════════════════════════════════
#  Part 3: FiPLSim 시뮬레이션 실행
# ════════════════════════════════════════════

def run_fiplsim():
    """FiPLSim 엔진으로 동일 조건 시뮬레이션"""
    from pipe_network import compare_dynamic_cases

    result = compare_dynamic_cases(
        num_branches=4,
        heads_per_branch=8,
        branch_spacing_m=3.5,
        head_spacing_m=2.3,
        inlet_pressure_mpa=1.4,
        total_flow_lpm=400.0,
        bead_height_existing=0.0,   # Case A도 비드 0mm
        bead_height_new=0.0,        # Case B도 비드 0mm
        beads_per_branch=0,         # 직관 비드 없음
    )

    case_B = result["case_B"]
    terminal = case_B["terminal_pressure_mpa"]
    seg_details = case_B["segment_details"]

    print("=" * 70)
    print("  PART 2: FiPLSim 시뮬레이션 결과 (비드 0mm, beads=0)")
    print("=" * 70)
    print(f"  최악 가지배관: B#{result['worst_branch_B']+1}")
    print(f"  말단 압력: {terminal:.6f} MPa")
    print()
    print(f"  {'구간':>4} {'관경':>5} {'유량':>10} {'유속':>10} {'Re':>10} "
          f"{'f':>10} {'주손실':>12} {'K1손실':>12} {'K2손실':>12} "
          f"{'총손실':>12} {'잔여압력':>12}")
    print("  " + "-" * 120)
    for s in seg_details:
        print(f"  {s['head_number']:>4} {s['pipe_size']:>5} {s['flow_lpm']:>10.2f} "
              f"{s['velocity_ms']:>10.4f} {s['reynolds']:>10.0f} "
              f"{s['friction_factor']:>10.6f} {s['major_loss_mpa']:>12.6f} "
              f"{s['K1_loss_mpa']:>12.6f} {s['K2_loss_mpa']:>12.6f} "
              f"{s['total_seg_loss_mpa']:>12.6f} {s['pressure_after_mpa']:>12.6f}")

    return terminal, seg_details


# ════════════════════════════════════════════
#  Part 4: 비교 검증
# ════════════════════════════════════════════

if __name__ == "__main__":
    print()
    hand_terminal, hand_details = run_hand_calc()
    print()
    sim_terminal, sim_details = run_fiplsim()

    print()
    print("=" * 70)
    print("  PART 3: 비교 검증 결과")
    print("=" * 70)
    print()

    # 구간별 비교
    print(f"  {'구간':>4} {'이론 잔여압력':>14} {'시뮬 잔여압력':>14} {'오차(MPa)':>12} {'일치':>6}")
    print("  " + "-" * 56)
    all_match = True
    for h_d, s_d in zip(hand_details, sim_details):
        err = abs(h_d["P"] - s_d["pressure_after_mpa"])
        match = err < 1e-4  # 0.0001 MPa = 0.1 kPa 허용
        if not match:
            all_match = False
        print(f"  {h_d['head']:>4} {h_d['P']:>14.6f} {s_d['pressure_after_mpa']:>14.6f} "
              f"{err:>12.8f} {'OK' if match else 'FAIL':>6}")

    print()
    err_terminal = abs(hand_terminal - sim_terminal)
    print(f"  이론 수계산 말단 압력:  {hand_terminal:.6f} MPa")
    print(f"  FiPLSim 시뮬 말단 압력: {sim_terminal:.6f} MPa")
    print(f"  절대 오차:              {err_terminal:.8f} MPa ({err_terminal*1e6:.2f} Pa)")
    print(f"  상대 오차:              {err_terminal/hand_terminal*100:.6f} %")
    print()

    if all_match and err_terminal < 1e-4:
        print("  ============================================")
        print("  RESULT: PASS — 이론 해석값과 완전 일치")
        print("  ============================================")
    else:
        print("  ============================================")
        print("  RESULT: FAIL — 불일치 발견")
        print("  ============================================")
    print()
