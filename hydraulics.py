# ! 소화배관 시뮬레이션 — 수리계산 엔진
# * Darcy-Weisbach 주손실, Colebrook-White 마찰계수, K-factor 부차손실

import math
from constants import RHO, G, NU, EPSILON_M

# ──────────────────────────────────────────────
# ? 레이놀즈 수 (Reynolds Number)
# ──────────────────────────────────────────────
def reynolds_number(velocity: float, diameter: float, nu: float = NU) -> float:
    """
    Re = V × D / ν
    velocity : 유속 (m/s)
    diameter : 내경 (m)
    nu       : 운동점성계수 (m²/s)
    """
    if diameter <= 0 or nu <= 0:
        return 0.0
    return velocity * diameter / nu


# ──────────────────────────────────────────────
# ? 유량 → 유속 변환
# ──────────────────────────────────────────────
def velocity_from_flow(Q_lpm: float, D_m: float) -> float:
    """
    원형 배관 내 유속 계산
    Q_lpm : 유량 (LPM, 리터/분)
    D_m   : 내경 (m)
    반환  : 유속 (m/s)
    """
    Q_m3s = Q_lpm / 60000.0  # LPM → m³/s
    A = math.pi * (D_m / 2.0) ** 2  # 단면적 (m²)
    if A <= 0:
        return 0.0
    return Q_m3s / A


# ──────────────────────────────────────────────
# ? Colebrook-White 마찰계수 (Friction Factor)
# ──────────────────────────────────────────────
def friction_factor(Re: float, epsilon: float = EPSILON_M, D: float = 0.05) -> float:
    """
    ! Colebrook-White 방정식을 반복법으로 풀어 Darcy 마찰계수를 구합니다.

    1/√f = -2.0 × log₁₀( (ε/D)/3.7 + 2.51/(Re×√f) )

    * 층류(Re < 2300): f = 64/Re
    * 난류: Swamee-Jain 근사식으로 초기값 → 최대 10회 고정점 반복

    Re      : 레이놀즈 수
    epsilon : 절대 조도 (m)
    D       : 내경 (m)
    반환    : Darcy 마찰계수 (무차원)
    """
    if Re <= 0:
        return 0.0
    if Re < 2300:
        return 64.0 / Re

    rel_rough = epsilon / D
    A = rel_rough / 3.7
    B = 2.51 / Re

    # * Swamee-Jain 근사식 (초기 추정값)
    log_arg = A + B / math.sqrt(0.02)
    if log_arg <= 0:
        log_arg = 1e-10
    f = 0.25 / (math.log10(log_arg)) ** 2

    # * 고정점 반복 (Fixed-point iteration)
    for _ in range(10):
        sqrt_f = math.sqrt(f)
        inner = A + B / sqrt_f
        if inner <= 0:
            inner = 1e-10
        rhs = -2.0 * math.log10(inner)
        if rhs == 0:
            break
        f_new = 1.0 / rhs ** 2
        if abs(f_new - f) / max(f, 1e-12) < 1e-8:
            f = f_new
            break
        f = f_new

    return f


# ──────────────────────────────────────────────
# ? 주손실 (Major Loss) — Darcy-Weisbach
# ──────────────────────────────────────────────
def major_loss(f: float, L: float, D: float, V: float) -> float:
    """
    h_f = f × (L/D) × (V² / 2g)

    f : Darcy 마찰계수
    L : 배관 길이 (m)
    D : 내경 (m)
    V : 유속 (m/s)
    반환 : 손실 수두 (m)
    """
    if D <= 0:
        return 0.0
    return f * (L / D) * (V ** 2 / (2.0 * G))


# ──────────────────────────────────────────────
# ? 부차 손실 (Minor Loss) — K-factor
# ──────────────────────────────────────────────
def minor_loss(K: float, V: float) -> float:
    """
    h_m = K × (V² / 2g)

    K : 손실 계수 (무차원)
    V : 유속 (m/s)
    반환 : 손실 수두 (m)
    """
    return K * (V ** 2 / (2.0 * G))


# ──────────────────────────────────────────────
# ? 비드 높이 → K1 계수 변환
# ──────────────────────────────────────────────
def k_welded_fitting(bead_height_mm: float, pipe_id_mm: float, base_K: float = 0.5) -> float:
    """
    ! 용접 비드 높이에 따른 배관이음쇠 K-factor 계산

    모델: K = base_K × (D / D_eff)⁴
    D_eff = D - 2 × bead_height  (양측 돌출 가정)

    * 비드 0mm → K = base_K (형상제어 신기술)
    * 비드 1.5mm → K 증가 (기존 용접 기술)

    bead_height_mm : 비드 돌출 높이 (mm)
    pipe_id_mm     : 배관 내경 (mm)
    base_K         : 비드 없을 때 기본 K값
    반환           : 조정된 K값
    """
    D = pipe_id_mm
    D_eff = D - 2.0 * bead_height_mm
    if D_eff <= 0:
        return float('inf')
    ratio = D / D_eff
    return base_K * ratio ** 4


# ──────────────────────────────────────────────
# ? 압력-수두 변환 유틸리티
# ──────────────────────────────────────────────
def head_to_mpa(h_meters: float, rho: float = RHO) -> float:
    """수두(m) → 압력(MPa) 변환: P = ρ × g × h / 10⁶"""
    return rho * G * h_meters / 1e6


def mpa_to_head(p_mpa: float, rho: float = RHO) -> float:
    """압력(MPa) → 수두(m) 변환: h = P × 10⁶ / (ρ × g)"""
    return p_mpa * 1e6 / (rho * G)
