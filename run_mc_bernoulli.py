"""
FiPLSim MC 10,000회 — 비드 존재 확률(p) 변화 시뮬레이션

조건 (고정):
  - 입구 압력: 0.4 MPa
  - 유량: 2,100 LPM (임계 유량)
  - 비드 높이: 2.5 mm
  - 배관: 4 가지배관 × 8 헤드 = 32 헤드
  - K_base: 0.5

변경 조건:
  - p = [0.1, 0.3, 0.5, 0.7, 0.9]  (비드 존재 확률)

각 MC 시행(trial)마다:
  1. 32개 접합부 각각에 대해 난수 생성 (0~1 균일분포)
  2. 난수 <= p 이면 비드 존재 (bead=2.5mm), 난수 > p 이면 비드 없음 (bead=0mm)
  3. FiPLSim 수리계산 수행 → 말단 수압 기록
  4. 이를 N = 10,000회 반복
"""
import sys, os, time
import numpy as np
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipe_network import generate_dynamic_system, calculate_dynamic_system
from constants import (
    PIPE_DIMENSIONS, K1_BASE, K2, K3,
    MIN_TERMINAL_PRESSURE_MPA,
)

# ══════════════════════════════════════
#  시뮬레이션 조건 (고정)
# ══════════════════════════════════════
N_ITERATIONS = 10000
INLET_PRESSURE = 0.40       # MPa
FLOW_RATE = 2100.0           # LPM
BEAD_HEIGHT = 2.5            # mm
NUM_BRANCHES = 4
HEADS_PER_BRANCH = 8
BRANCH_SPACING = 3.5         # m
HEAD_SPACING = 2.3            # m
BEADS_PER_BRANCH = 8         # 가지배관당 용접 비드 8개

# 변경 조건: 비드 존재 확률
P_VALUES = [0.1, 0.3, 0.5, 0.7, 0.9]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MC_Bernoulli_Results")
os.makedirs(OUT_DIR, exist_ok=True)

rng = np.random.default_rng()
total_fittings = NUM_BRANCHES * HEADS_PER_BRANCH  # 32개

print("=" * 70)
print("  FiPLSim MC Bernoulli — 비드 존재 확률(p) 변화 시뮬레이션")
print("=" * 70)
print(f"  입구 압력: {INLET_PRESSURE} MPa")
print(f"  유량: {FLOW_RATE} LPM")
print(f"  비드 높이: {BEAD_HEIGHT} mm")
print(f"  배관: {NUM_BRANCHES} × {HEADS_PER_BRANCH} = {total_fittings} 접합부")
print(f"  MC 반복: {N_ITERATIONS:,}회 × {len(P_VALUES)}개 확률 수준")
print(f"  K_base: {K1_BASE}")
print("=" * 70)
print()

summary_rows = []

for run_idx, p_val in enumerate(P_VALUES):
    label = f"p{p_val:.1f}"
    print(f"  [Run {run_idx+1}/{len(P_VALUES)}] p = {p_val} ... ", end="", flush=True)

    t0 = time.time()
    worst_pressures = np.zeros(N_ITERATIONS)
    bead_counts = np.zeros(N_ITERATIONS, dtype=int)
    all_configs = []

    common = dict(
        num_branches=NUM_BRANCHES,
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING,
        head_spacing_m=HEAD_SPACING,
        inlet_pressure_mpa=INLET_PRESSURE,
        total_flow_lpm=FLOW_RATE,
        K1_base=K1_BASE,
        K2_val=K2,
    )

    for trial in range(N_ITERATIONS):
        # ── 베르누이 비드 배치: 각 접합부 독립적으로 확률 p ──
        rand_vals = rng.uniform(0, 1, size=(NUM_BRANCHES, HEADS_PER_BRANCH))
        beads_2d = [[0.0] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
        bead_positions = []

        for b in range(NUM_BRANCHES):
            for h in range(HEADS_PER_BRANCH):
                if rand_vals[b][h] <= p_val:
                    beads_2d[b][h] = BEAD_HEIGHT
                    bead_positions.append((b, h))

        bead_counts[trial] = len(bead_positions)

        # FiPLSim 수리계산
        system = generate_dynamic_system(
            bead_heights_2d=beads_2d,
            beads_per_branch=BEADS_PER_BRANCH,
            rng=None,
            **common,
        )
        result = calculate_dynamic_system(system, K3)
        worst_pressures[trial] = result["worst_terminal_mpa"]
        all_configs.append(bead_positions)

    elapsed = time.time() - t0

    # ── 누적 통계 ──
    cum_mean = np.cumsum(worst_pressures) / np.arange(1, N_ITERATIONS + 1)
    cum_std = np.array([
        float(np.std(worst_pressures[:i+1], ddof=1)) if i > 0 else 0.0
        for i in range(N_ITERATIONS)
    ])
    cum_min = np.minimum.accumulate(worst_pressures)
    cum_max = np.maximum.accumulate(worst_pressures)
    cum_pf = np.cumsum(worst_pressures < MIN_TERMINAL_PRESSURE_MPA) / np.arange(1, N_ITERATIONS + 1) * 100.0

    mean_bead_count = float(np.mean(bead_counts))
    mean_p = float(np.mean(worst_pressures))
    std_p = float(np.std(worst_pressures, ddof=1))
    min_p = float(np.min(worst_pressures))
    max_p = float(np.max(worst_pressures))
    pf_pct = float(cum_pf[-1])

    print(f"{elapsed:.1f}초 | 평균 비드={mean_bead_count:.1f}개 | "
          f"μ={mean_p:.4f} | σ={std_p:.6f} | Pf={pf_pct:.1f}%")

    # ── 개별 CSV 저장 ──
    csv_path = os.path.join(OUT_DIR, f"MC_Bernoulli_{label}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([f"FiPLSim MC Bernoulli — p = {p_val} (비드 존재 확률)"])
        w.writerow([f"입구 압력: {INLET_PRESSURE} MPa | 유량: {FLOW_RATE} LPM | "
                     f"비드: {BEAD_HEIGHT}mm | 접합부: {total_fittings}개"])
        w.writerow([f"기대 비드 수: {total_fittings} x {p_val} = {total_fittings * p_val:.1f}개 | "
                     f"실제 평균: {mean_bead_count:.1f}개"])
        w.writerow([])
        w.writerow([
            "Trial",
            "말단 압력 (MPa)",
            "비드 개수",
            "비드 위치 (가지배관, 헤드)",
            "누적 평균 (μ, MPa)",
            "누적 표준편차 (σ, MPa)",
            "누적 최솟값 (Min, MPa)",
            "누적 최댓값 (Max, MPa)",
            "규정 미달 확률 (Pf, %)",
        ])
        for i in range(N_ITERATIONS):
            w.writerow([
                i + 1,
                f"{worst_pressures[i]:.6f}",
                bead_counts[i],
                str(all_configs[i]),
                f"{cum_mean[i]:.6f}",
                f"{cum_std[i]:.6f}",
                f"{cum_min[i]:.6f}",
                f"{cum_max[i]:.6f}",
                f"{cum_pf[i]:.2f}",
            ])
        w.writerow([])
        w.writerow(["=== 최종 통계 ==="])
        w.writerow(["평균 말단 압력 (MPa)", f"{mean_p:.6f}"])
        w.writerow(["표준편차 (MPa)", f"{std_p:.6f}"])
        w.writerow(["최솟값 (MPa)", f"{min_p:.6f}"])
        w.writerow(["최댓값 (MPa)", f"{max_p:.6f}"])
        w.writerow(["규정 미달 확률 (%)", f"{pf_pct:.2f}"])
        w.writerow(["평균 비드 개수", f"{mean_bead_count:.1f}"])
        w.writerow(["기대 비드 개수", f"{total_fittings * p_val:.1f}"])
        w.writerow(["시행 횟수", N_ITERATIONS])

    # 요약 수집
    summary_rows.append({
        "Run": f"Run {run_idx+1}",
        "p (비드 확률)": p_val,
        "의미": ["우수한 시공 품질", "양호한 시공 품질", "기본 설정",
                 "열악한 시공 품질", "매우 열악한 시공 품질"][run_idx],
        "기대 비드 수": round(total_fittings * p_val, 1),
        "실측 평균 비드 수": round(mean_bead_count, 1),
        "평균 수압 (MPa)": round(mean_p, 6),
        "표준편차 (MPa)": round(std_p, 6),
        "최솟값 (MPa)": round(min_p, 6),
        "최댓값 (MPa)": round(max_p, 6),
        "규정 미달 Pf (%)": round(pf_pct, 2),
        "판정": "PASS" if pf_pct == 0.0 else f"FAIL ({pf_pct:.1f}%)",
    })

# ── 전체 요약 CSV ──
summary_path = os.path.join(OUT_DIR, "MC_Bernoulli_Summary.csv")
with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["FiPLSim MC Bernoulli — 비드 존재 확률(p) 5개 수준 요약"])
    w.writerow([f"입구 압력: {INLET_PRESSURE} MPa | 유량: {FLOW_RATE} LPM | "
                 f"비드: {BEAD_HEIGHT}mm | 접합부: {total_fittings}개 | "
                 f"N = {N_ITERATIONS:,}회"])
    w.writerow([])
    headers = list(summary_rows[0].keys())
    w.writerow(headers)
    for row in summary_rows:
        w.writerow([row[h] for h in headers])

print()
print("=" * 70)
print("  전체 요약")
print("=" * 70)
print(f"  {'Run':>5} {'p':>5} {'의미':>16} {'기대비드':>8} {'μ(MPa)':>10} "
      f"{'σ(MPa)':>10} {'Min':>8} {'Max':>8} {'Pf(%)':>8}")
print("  " + "-" * 90)
for r in summary_rows:
    print(f"  {r['Run']:>5} {r['p (비드 확률)']:>5.1f} {r['의미']:>16} "
          f"{r['기대 비드 수']:>8.1f} {r['평균 수압 (MPa)']:>10.4f} "
          f"{r['표준편차 (MPa)']:>10.6f} {r['최솟값 (MPa)']:>8.4f} "
          f"{r['최댓값 (MPa)']:>8.4f} {r['규정 미달 Pf (%)']:>8.2f}")
print()
print(f"  저장 위치: {OUT_DIR}")
print("=" * 70)
