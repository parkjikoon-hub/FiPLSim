"""
FiPLSim MC 10,000회 배치 시뮬레이션
조건: 비드 [0.0, 2.0, 2.5]mm × 유량 [1200, 1600, 2100] LPM = 9가지
출력: 각 조건별 CSV (Trial별 말단 압력 + 누적 통계)
     + 전체 요약 CSV
"""
import sys, os, time
import numpy as np
import csv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulation import run_dynamic_monte_carlo
from constants import MIN_TERMINAL_PRESSURE_MPA

# ── 시뮬레이션 조건 ──
N_ITERATIONS = 10000
BEAD_HEIGHTS = [0.0, 2.0, 2.5]        # mm
FLOW_RATES = [1200, 1600, 2100]        # LPM
NUM_BRANCHES = 4
HEADS_PER_BRANCH = 8
BRANCH_SPACING = 3.5
HEAD_SPACING = 2.3
INLET_PRESSURE = 0.40                  # MPa (스크린샷 기준)
BEADS_PER_BRANCH = 8
MIN_DEFECTS = 1
MAX_DEFECTS = 3

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MC_Results")
os.makedirs(OUT_DIR, exist_ok=True)

summary_rows = []
total_cases = len(BEAD_HEIGHTS) * len(FLOW_RATES)
case_num = 0

print("=" * 70)
print(f"  FiPLSim MC Batch — {N_ITERATIONS:,}회 × {total_cases}가지 조건")
print(f"  배관: {NUM_BRANCHES} 가지배관 × {HEADS_PER_BRANCH} 헤드 = {NUM_BRANCHES*HEADS_PER_BRANCH} 헤드")
print(f"  입구 압력: {INLET_PRESSURE} MPa")
print(f"  용접 비드: 가지배관당 {BEADS_PER_BRANCH}개")
print("=" * 70)
print()

for bead in BEAD_HEIGHTS:
    for flow in FLOW_RATES:
        case_num += 1
        label = f"bead{bead:.1f}mm_flow{flow}LPM"
        print(f"  [{case_num}/{total_cases}] 비드 {bead}mm, 유량 {flow} LPM ... ", end="", flush=True)

        t0 = time.time()
        mc = run_dynamic_monte_carlo(
            n_iterations=N_ITERATIONS,
            min_defects=MIN_DEFECTS,
            max_defects=MAX_DEFECTS,
            bead_height_mm=bead,
            num_branches=NUM_BRANCHES,
            heads_per_branch=HEADS_PER_BRANCH,
            branch_spacing_m=BRANCH_SPACING,
            head_spacing_m=HEAD_SPACING,
            inlet_pressure_mpa=INLET_PRESSURE,
            total_flow_lpm=float(flow),
            beads_per_branch=BEADS_PER_BRANCH,
            topology="tree",
        )
        elapsed = time.time() - t0

        tp = mc["terminal_pressures"]
        tp_arr = np.array(tp)

        # 누적 통계 계산
        cum_mean = np.cumsum(tp_arr) / np.arange(1, N_ITERATIONS + 1)
        cum_std = np.array([
            float(np.std(tp_arr[:i+1], ddof=1)) if i > 0 else 0.0
            for i in range(N_ITERATIONS)
        ])
        cum_min = np.minimum.accumulate(tp_arr)
        cum_max = np.maximum.accumulate(tp_arr)
        cum_pf = np.cumsum(tp_arr < MIN_TERMINAL_PRESSURE_MPA) / np.arange(1, N_ITERATIONS + 1) * 100.0

        # 개별 CSV 저장
        csv_path = os.path.join(OUT_DIR, f"MC_{label}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([f"FiPLSim MC {N_ITERATIONS:,}회 — 비드 {bead}mm, 유량 {flow} LPM"])
            w.writerow([f"입구 압력: {INLET_PRESSURE} MPa | 배관: {NUM_BRANCHES}x{HEADS_PER_BRANCH} | 용접비드: {BEADS_PER_BRANCH}개/가지배관"])
            w.writerow([])
            w.writerow([
                "Trial",
                "말단 압력 (MPa)",
                "결함 위치",
                "누적 평균 (μ, MPa)",
                "누적 표준편차 (σ, MPa)",
                "누적 최솟값 (Min, MPa)",
                "누적 최댓값 (Max, MPa)",
                "규정 미달 확률 (Pf, %)",
            ])
            for i in range(N_ITERATIONS):
                w.writerow([
                    i + 1,
                    f"{tp[i]:.6f}",
                    str(mc["defect_configs"][i]),
                    f"{cum_mean[i]:.6f}",
                    f"{cum_std[i]:.6f}",
                    f"{cum_min[i]:.6f}",
                    f"{cum_max[i]:.6f}",
                    f"{cum_pf[i]:.2f}",
                ])
            # 최종 통계
            w.writerow([])
            w.writerow(["=== 최종 통계 ==="])
            w.writerow(["평균 (Mean)", f"{float(np.mean(tp_arr)):.6f}"])
            w.writerow(["표준편차 (Std)", f"{float(np.std(tp_arr, ddof=1)):.6f}"])
            w.writerow(["최솟값 (Min)", f"{float(np.min(tp_arr)):.6f}"])
            w.writerow(["최댓값 (Max)", f"{float(np.max(tp_arr)):.6f}"])
            w.writerow(["규정 미달 확률", f"{float(cum_pf[-1]):.2f}%"])
            w.writerow(["시행 횟수", N_ITERATIONS])

        # 요약 데이터 수집
        summary_rows.append({
            "비드 (mm)": bead,
            "유량 (LPM)": flow,
            "평균 (MPa)": round(float(np.mean(tp_arr)), 6),
            "표준편차 (MPa)": round(float(np.std(tp_arr, ddof=1)), 6),
            "최솟값 (MPa)": round(float(np.min(tp_arr)), 6),
            "최댓값 (MPa)": round(float(np.max(tp_arr)), 6),
            "규정 미달 (%)": round(float(cum_pf[-1]), 2),
            "PASS/FAIL": "PASS" if float(np.mean(tp_arr)) >= MIN_TERMINAL_PRESSURE_MPA else "FAIL",
        })

        print(f"{elapsed:.1f}초 | 평균={float(np.mean(tp_arr)):.4f} MPa | Pf={float(cum_pf[-1]):.1f}%")

# ── 전체 요약 CSV ──
summary_path = os.path.join(OUT_DIR, "MC_Summary.csv")
with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow([f"FiPLSim MC {N_ITERATIONS:,}회 — 전체 조건 요약"])
    w.writerow([f"입구 압력: {INLET_PRESSURE} MPa | 배관: {NUM_BRANCHES}x{HEADS_PER_BRANCH} | 용접비드: {BEADS_PER_BRANCH}개/가지배관"])
    w.writerow([])
    headers = list(summary_rows[0].keys())
    w.writerow(headers)
    for row in summary_rows:
        w.writerow([row[h] for h in headers])

print()
print("=" * 70)
print(f"  완료! CSV {total_cases + 1}개 파일 저장됨")
print(f"  저장 위치: {OUT_DIR}")
print()
for bead in BEAD_HEIGHTS:
    for flow in FLOW_RATES:
        print(f"    MC_bead{bead:.1f}mm_flow{flow}LPM.csv")
print(f"    MC_Summary.csv (전체 요약)")
print("=" * 70)
