"""
베르누이 MC 배치 시뮬레이션 — 비드높이 0mm (형상제어 신기술)
5 p_b x 3 유량 x 1 비드 높이(0mm) = 15개 조건, 각 10,000회 MC

고정 조건:
  - Tree 토폴로지, 4 가지배관, 8 헤드/가지배관
  - 가지배관 간격 3.5m, 헤드 간격 2.3m
  - 입구 압력 0.4 MPa
  - 가지배관당 용접 비드 8개
  - 비드 높이 0mm (형상제어 신기술 — 비드 돌출 없음)
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulation import run_bernoulli_monte_carlo

# ── 시뮬레이션 조건 ──
N_ITERATIONS = 10000
P_VALUES = [0.1, 0.3, 0.5, 0.7, 0.9]
FLOW_RATES = [1200, 1600, 2100]
BEAD_HEIGHTS = [0.0]  # 형상제어 신기술: 비드 높이 0mm

# 고정 조건
INLET_PRESSURE = 0.40
NUM_BRANCHES = 4
HEADS_PER_BRANCH = 8
BRANCH_SPACING = 3.5
HEAD_SPACING = 2.3
BEADS_PER_BRANCH = 8
TOTAL_FITTINGS = NUM_BRANCHES * HEADS_PER_BRANCH  # 32

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MC_Bernoulli_Batch_Bead0")
os.makedirs(OUT_DIR, exist_ok=True)

total_conditions = len(P_VALUES) * len(FLOW_RATES) * len(BEAD_HEIGHTS)

print("=" * 70)
print("  FiPLSim Bernoulli MC Batch — Bead Height 0mm (New Tech)")
print(f"  {len(P_VALUES)} p x {len(FLOW_RATES)} flows x {len(BEAD_HEIGHTS)} bead = "
      f"{total_conditions} conditions x {N_ITERATIONS:,} iterations")
print("=" * 70)

# ── 요약 테이블 수집 ──
summary_rows = []
condition_idx = 0
t_start_all = time.time()

for flow in FLOW_RATES:
    for bead in BEAD_HEIGHTS:
        for p_val in P_VALUES:
            condition_idx += 1
            label = f"Q={flow}_B={bead}_p={p_val:.1f}"
            print(f"\n[{condition_idx}/{total_conditions}] {label} ... ", end="", flush=True)

            t0 = time.time()
            res = run_bernoulli_monte_carlo(
                p_bead=p_val,
                n_iterations=N_ITERATIONS,
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
            print(f"{elapsed:.1f}s  mu={res['mean_pressure']:.4f}  Pf={res['p_below_threshold']*100:.2f}%")

            # 요약 행 추가
            summary_rows.append({
                "유량 (LPM)": flow,
                "비드 높이 (mm)": bead,
                "p_b": p_val,
                "기대 비드 수": res["expected_bead_count"],
                "실측 평균 비드 수": round(res["mean_bead_count"], 1),
                "평균 수압 (MPa)": round(res["mean_pressure"], 6),
                "표준편차 (MPa)": round(res["std_pressure"], 6),
                "최솟값 (MPa)": round(res["min_pressure"], 6),
                "최댓값 (MPa)": round(res["max_pressure"], 6),
                "Pf (%)": round(res["p_below_threshold"] * 100, 2),
                "판정": "PASS" if res["p_below_threshold"] == 0 else "FAIL",
            })

            # 개별 CSV (trial별 데이터)
            tp = np.array(res["terminal_pressures"])
            bc = np.array(res["bead_counts"])
            n = len(tp)
            cum_mean = np.cumsum(tp) / np.arange(1, n + 1)
            cum_std = np.array([float(np.std(tp[:j+1], ddof=1)) if j > 0 else 0.0 for j in range(n)])
            cum_min = np.minimum.accumulate(tp)
            cum_max = np.maximum.accumulate(tp)
            cum_pf = np.cumsum(tp < 0.1) / np.arange(1, n + 1) * 100.0

            df_detail = pd.DataFrame({
                "Trial": range(1, n + 1),
                "말단 수압 (MPa)": tp,
                "비드 개수": bc,
                "누적 평균 (MPa)": cum_mean,
                "누적 표준편차 (MPa)": cum_std,
                "누적 최솟값 (MPa)": cum_min,
                "누적 최댓값 (MPa)": cum_max,
                "규정 미달 확률 (%)": cum_pf,
            })
            detail_path = os.path.join(OUT_DIR, f"Bernoulli_{label}.csv")
            df_detail.to_csv(detail_path, index=False, encoding="utf-8-sig")

# ── 전체 요약 CSV ──
df_summary = pd.DataFrame(summary_rows)
summary_path = os.path.join(OUT_DIR, "Bernoulli_Summary_Bead0.csv")
df_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")

# ── Excel (요약 + 조건별 피벗) ──
excel_path = os.path.join(OUT_DIR, "Bernoulli_Batch_Bead0_Results.xlsx")
with pd.ExcelWriter(excel_path, engine="openpyxl") as w:
    # Sheet 1: 전체 요약
    df_summary.to_excel(w, sheet_name="전체 요약", index=False)

    # Sheet 2: 평균 수압 피벗 (유량 x p_b)
    pivot_mean = df_summary.pivot_table(
        index=["유량 (LPM)"],
        columns="p_b",
        values="평균 수압 (MPa)",
    )
    pivot_mean.columns = [f"p={c:.1f}" for c in pivot_mean.columns]
    pivot_mean.to_excel(w, sheet_name="평균 수압 피벗")

    # Sheet 3: 표준편차 피벗
    pivot_std = df_summary.pivot_table(
        index=["유량 (LPM)"],
        columns="p_b",
        values="표준편차 (MPa)",
    )
    pivot_std.columns = [f"p={c:.1f}" for c in pivot_std.columns]
    pivot_std.to_excel(w, sheet_name="표준편차 피벗")

    # Sheet 4: Pf 피벗
    pivot_pf = df_summary.pivot_table(
        index=["유량 (LPM)"],
        columns="p_b",
        values="Pf (%)",
    )
    pivot_pf.columns = [f"p={c:.1f}" for c in pivot_pf.columns]
    pivot_pf.to_excel(w, sheet_name="Pf (%) 피벗")

    # Sheet 5: 최솟값 피벗
    pivot_min = df_summary.pivot_table(
        index=["유량 (LPM)"],
        columns="p_b",
        values="최솟값 (MPa)",
    )
    pivot_min.columns = [f"p={c:.1f}" for c in pivot_min.columns]
    pivot_min.to_excel(w, sheet_name="최솟값 피벗")

    # Sheet 6: 입력 조건
    pd.DataFrame([{
        "배관망 구조": "Tree (가지형)",
        "가지배관 수": NUM_BRANCHES,
        "가지배관당 헤드 수": HEADS_PER_BRANCH,
        "총 접합부": TOTAL_FITTINGS,
        "가지배관 간격 (m)": BRANCH_SPACING,
        "헤드 간격 (m)": HEAD_SPACING,
        "입구 압력 (MPa)": INLET_PRESSURE,
        "가지배관당 용접 비드": BEADS_PER_BRANCH,
        "MC 반복 횟수": N_ITERATIONS,
        "p_b 수준": str(P_VALUES),
        "유량 수준 (LPM)": str(FLOW_RATES),
        "비드 높이 수준 (mm)": str(BEAD_HEIGHTS),
        "비고": "비드 높이 0mm — 형상제어 신기술 (비드 돌출 없음)",
    }]).to_excel(w, sheet_name="입력 조건", index=False)

elapsed_all = time.time() - t_start_all
print("\n" + "=" * 70)
print(f"  완료! 총 소요시간: {elapsed_all:.1f}초")
print(f"  요약 CSV: {summary_path}")
print(f"  Excel:    {excel_path}")
print(f"  개별 CSV: {OUT_DIR}/ ({total_conditions}개 파일)")
print("=" * 70)

# ── 요약 출력 ──
print("\n[ 전체 요약 테이블 — 비드높이 0mm ]")
print(f"{'유량':>6} {'비드':>5} {'p_b':>5} {'기대비드':>8} {'평균(MPa)':>11} {'sigma':>10} {'최솟값':>10} {'최댓값':>10} {'Pf(%)':>7} {'판정':>5}")
print("-" * 90)
for r in summary_rows:
    print(f"{r['유량 (LPM)']:>6} {r['비드 높이 (mm)']:>5.1f} {r['p_b']:>5.1f} "
          f"{r['기대 비드 수']:>8.1f} {r['평균 수압 (MPa)']:>11.6f} {r['표준편차 (MPa)']:>10.6f} "
          f"{r['최솟값 (MPa)']:>10.6f} {r['최댓값 (MPa)']:>10.6f} {r['Pf (%)']:>7.2f} {r['판정']:>5}")
