# ! 소화배관 시뮬레이션 — 전역 상수 및 기본 파라미터 정의
# * 모든 모듈이 이 파일을 참조합니다.

import math

# ──────────────────────────────────────────────
# ? 유체 물성치 (물, 20°C 기준)
# ──────────────────────────────────────────────
RHO = 998.0          # 밀도 (kg/m³)
MU = 1.002e-3        # 동점성계수 (Pa·s)
NU = MU / RHO        # 운동점성계수 (m²/s) ≈ 1.004e-6
G = 9.81             # 중력가속도 (m/s²)

# ──────────────────────────────────────────────
# ? 배관 물성치 (탄소강 강관, Carbon Steel)
# ──────────────────────────────────────────────
EPSILON_MM = 0.045   # 절대 조도 (mm)
EPSILON_M = EPSILON_MM / 1000.0  # 절대 조도 (m)

# ──────────────────────────────────────────────
# ? JIS/KS Schedule 40 배관 치수 테이블
#   key = 호칭 구경 (e.g. "25A")
#   value = {"od_mm", "wall_mm", "id_mm"}
# ──────────────────────────────────────────────
PIPE_DIMENSIONS = {
    "25A":  {"od_mm":  33.40, "wall_mm": 3.38, "id_mm":  26.64},
    "32A":  {"od_mm":  42.16, "wall_mm": 3.56, "id_mm":  35.04},
    "40A":  {"od_mm":  48.26, "wall_mm": 3.68, "id_mm":  40.90},
    "50A":  {"od_mm":  60.33, "wall_mm": 3.91, "id_mm":  52.51},
    "65A":  {"od_mm":  73.03, "wall_mm": 5.16, "id_mm":  62.71},
    "80A":  {"od_mm":  88.90, "wall_mm": 5.49, "id_mm":  77.92},
    "100A": {"od_mm": 114.30, "wall_mm": 6.02, "id_mm": 102.26},
}

def get_inner_diameter_m(nominal_size: str) -> float:
    """호칭 구경으로부터 내경(m)을 반환합니다."""
    return PIPE_DIMENSIONS[nominal_size]["id_mm"] / 1000.0

# ──────────────────────────────────────────────
# ? K-factor 기본값
# ──────────────────────────────────────────────
K1_BASE = 0.5        # 배관이음쇠 (Welded Fitting) 기본 K — 비드 0mm 기준
K2 = 2.5             # 헤드이음쇠 (Head Fitting) 고유 저항 계수 (상수)
K3 = 1.0             # 분기 손실 (교차배관 → 가지배관 분기 입구, Tee-Branch)
K_TEE_RUN = 0.3      # 교차배관 직진 손실 (Tee-Run, 분기 후 직진 흐름)

# ──────────────────────────────────────────────
# ? Hardy-Cross 수렴 파라미터 (Full Grid 배관망용)
# ──────────────────────────────────────────────
HC_MAX_ITERATIONS = 1000     # 최대 반복 횟수 (대규모 안전 마진)
HC_TOLERANCE_M = 0.001       # 루프 수두 균형 허용 오차 (m) ≈ 0.01 kPa
HC_TOLERANCE_LPM = 0.0001   # 유량 보정값 수렴 허용 오차 (LPM)
HC_RELAXATION_FACTOR = 0.5   # Under-relaxation 감쇠 계수 (기본 0.5, UI에서 0.1~1.0 조절 가능)
HC_RELAXATION_MIN = 0.1      # 이완 계수 최솟값
HC_RELAXATION_MAX = 1.0      # 이완 계수 최댓값

# ──────────────────────────────────────────────
# ? 자동 관경 선정 규칙 (NFSC 103 기반)
#   하류 헤드 수에 따른 배관 구경 결정
# ──────────────────────────────────────────────
def auto_pipe_size(num_heads_downstream: int) -> str:
    """
    ! 하류 헤드 수 기준 자동 배관 구경 선정

    NFSC 103 소화설비 배관 설계 기준:
      12개 이상 → 65A
      6~11개   → 50A
      4~5개    → 40A
      3개      → 32A
      1~2개    → 25A
    """
    if num_heads_downstream >= 12:
        return "65A"
    elif num_heads_downstream >= 6:
        return "50A"
    elif num_heads_downstream >= 4:
        return "40A"
    elif num_heads_downstream >= 3:
        return "32A"
    else:
        return "25A"

def auto_cross_main_size(total_heads: int) -> str:
    """
    ! 전체 헤드 수 기준 교차배관 구경 자동 선정
      40개 이상 → 100A
      20개 이상 → 80A
      그 외     → 65A
    """
    if total_heads >= 40:
        return "100A"
    elif total_heads >= 20:
        return "80A"
    else:
        return "65A"

# ──────────────────────────────────────────────
# ? 레거시: 고정 8헤드 매핑 (하위 호환용)
#   PRD 원본: 50A(3개) → 40A(2개) → 32A(1개) → 25A(2개)
# ──────────────────────────────────────────────
NUM_HEADS = 8

PIPE_ASSIGNMENT = {
    0: "50A", 1: "50A", 2: "50A",
    3: "40A", 4: "40A",
    5: "32A",
    6: "25A", 7: "25A",
}

# ──────────────────────────────────────────────
# ? 동적 배관망 기본 파라미터
# ──────────────────────────────────────────────
DEFAULT_NUM_BRANCHES = 4          # 양방향 가지배관 총 개수
DEFAULT_HEADS_PER_BRANCH = 8      # 가지배관당 헤드 수
DEFAULT_BRANCH_SPACING_M = 3.5    # 가지배관 사이 간격 (교차배관 위, m)
DEFAULT_HEAD_SPACING_M = 2.3      # 헤드 사이 간격 (가지배관 위, m)
MAX_BRANCHES = 200                # 최대 허용 가지배관 수
MAX_HEADS_PER_BRANCH = 50         # 최대 허용 가지배관당 헤드 수

# ──────────────────────────────────────────────
# ? 용접 비드 국부 손실 파라미터
#   가지배관 직관 구간 내 무작위 배치되는 용접 비드 개수
# ──────────────────────────────────────────────
DEFAULT_BEADS_PER_BRANCH = 5      # 가지배관당 용접 비드 기본 개수
MAX_BEADS_PER_BRANCH = 20         # 가지배관당 용접 비드 최대 개수

# ──────────────────────────────────────────────
# ? 기본 시뮬레이션 파라미터
# ──────────────────────────────────────────────
DEFAULT_INLET_PRESSURE_MPA = 1.4
DEFAULT_TOTAL_FLOW_LPM = 400.0
DEFAULT_FITTING_SPACING_M = 2.3
FITTING_SPACING_OPTIONS = [1.7, 2.1, 2.3, 2.5, 3.2]  # 선택 가능한 이음쇠 간격 (m)
DEFAULT_BEAD_HEIGHT_MM = 1.5       # 기존 용접 기술
NEW_TECH_BEAD_HEIGHT_MM = 0.0      # 형상제어 신기술
MIN_TERMINAL_PRESSURE_MPA = 0.1    # 말단 최소 방수압 기준
MAX_TERMINAL_PRESSURE_MPA = 1.2    # 말단 최대 방수압 기준 (NFPC)

# ──────────────────────────────────────────────
# ? NFPC 유속 제한 기준
# ──────────────────────────────────────────────
MAX_VELOCITY_BRANCH_MS = 6.0       # 가지배관 최대 유속 (m/s) — NFPC
MAX_VELOCITY_OTHER_MS = 10.0       # 교차배관/기타 최대 유속 (m/s) — NFPC

# ──────────────────────────────────────────────
# ? 몬테카를로 기본값
# ──────────────────────────────────────────────
DEFAULT_MC_ITERATIONS = 100
DEFAULT_MIN_DEFECTS = 1
DEFAULT_MAX_DEFECTS = 3

# ──────────────────────────────────────────────
# ? 베르누이 몬테카를로 기본값
# ──────────────────────────────────────────────
DEFAULT_BERNOULLI_P_VALUES = [0.1, 0.3, 0.5, 0.7, 0.9]
DEFAULT_BERNOULLI_MC_ITERATIONS = 1000
BERNOULLI_P_MIN = 0.01
BERNOULLI_P_MAX = 0.99

# ──────────────────────────────────────────────
# ? 경제성 분석 기본값
# ──────────────────────────────────────────────
DEFAULT_OPERATING_HOURS_PER_YEAR = 2000.0
DEFAULT_ELECTRICITY_RATE_KRW = 120.0  # KRW/kWh

# ──────────────────────────────────────────────
# ? 펌프 성능 데이터베이스
#   P-Q 곡선 포인트: (유량 LPM, 양정 m)
# ──────────────────────────────────────────────
PUMP_DATABASE = {
    "Model A - Wilo Helix-V": {
        "description": "고효율형 (High-Efficiency)",
        "rated_flow_lpm": 1000,
        "rated_head_m": 80,
        "efficiency": 0.78,
        "pq_points": [(0, 115), (500, 105), (1000, 80), (1200, 65), (1500, 48)],
    },
    "Model B - ShinShin SSV": {
        "description": "범용형 (General Purpose)",
        "rated_flow_lpm": 800,
        "rated_head_m": 70,
        "efficiency": 0.68,
        "pq_points": [(0, 98), (400, 90), (800, 70), (1000, 55), (1200, 40)],
    },
    "Model C - Daeyoung DVM": {
        "description": "소규모형 (Small Scale)",
        "rated_flow_lpm": 500,
        "rated_head_m": 60,
        "efficiency": 0.62,
        "pq_points": [(0, 84), (250, 75), (500, 60), (750, 40)],
    },
}
