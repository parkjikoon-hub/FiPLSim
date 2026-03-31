#!/usr/bin/env python3
"""
uni_bi_simulation_results CSV → 논문용 그래프 생성
실행: PYTHONIOENCODING=utf-8 python3 plot_uni_bi_results.py
"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from pathlib import Path
import warnings, sys, argparse

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ── 한글 폰트 설정 ──────────────────────────────────────
_KOREAN_FONTS = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "AppleGothic"]
for _fname in _KOREAN_FONTS:
    if any(_fname in f.name for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _fname
        plt.rcParams["axes.unicode_minus"] = False
        break

# ── 경로 ────────────────────────────────────────────────
DATA_DIR = Path("uni_bi_simulation_results/data")
FIG_DIR  = Path("uni_bi_simulation_results/figures")
FIG_DIR.mkdir(parents=True, exist_ok=True)

# ── 공통 스타일 ─────────────────────────────────────────
TOPO_COLORS = {"uni": "#1f77b4", "bi": "#d62728"}
TOPO_LABELS = {"uni": "단방향 (4가지)", "bi": "양방향 (2+2가지)"}
TOPO_MARKERS = {"uni": "o", "bi": "s"}
DPI = 200
PASS_LINE = 0.1  # MPa


def save(fig, name):
    path = FIG_DIR / name
    fig.savefig(path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"  [저장] {path}")


# ═══════════════════════════════════════════════════════════
#  1. A1 — 말단압력 vs 비드높이 × 결함개수
# ═══════════════════════════════════════════════════════════
def plot_A1():
    df = pd.read_csv(DATA_DIR / "A1_design_flow_deterministic.csv")
    defects = sorted(df["defect_count"].unique())
    fig, axes = plt.subplots(1, len(defects), figsize=(3.5 * len(defects), 4),
                             sharey=True)
    if len(defects) == 1:
        axes = [axes]
    for ax, dc in zip(axes, defects):
        for topo in ["uni", "bi"]:
            sub = df[(df["topology"] == topo) & (df["defect_count"] == dc)]
            sub = sub.sort_values("bead_height_mm")
            ax.plot(sub["bead_height_mm"], sub["terminal_mpa"],
                    color=TOPO_COLORS[topo], marker=TOPO_MARKERS[topo],
                    label=TOPO_LABELS[topo], linewidth=1.5, markersize=5)
        ax.axhline(PASS_LINE, color="gray", ls="--", lw=0.8, label="PASS 기준 (0.1 MPa)")
        ax.set_title(f"결함 {dc}개", fontsize=11)
        ax.set_xlabel("비드 높이 (mm)")
        if ax == axes[0]:
            ax.set_ylabel("말단 압력 (MPa)")
    axes[-1].legend(fontsize=8, loc="lower left")
    fig.suptitle("A-1: 설계유량(2560 LPM) 결정론 비교", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "A1_terminal_vs_bead_by_defect.png")


# ═══════════════════════════════════════════════════════════
#  2. A2 — 말단압력 vs 유량 (주요 조건)
# ═══════════════════════════════════════════════════════════
def plot_A2():
    df = pd.read_csv(DATA_DIR / "A2_flow_sweep_deterministic.csv")
    # 대표 조건: defect=0,2,4 / bead=0,1.5,3.0
    key_combos = [(0, 0.0), (2, 1.5), (4, 3.0)]
    fig, ax = plt.subplots(figsize=(8, 5))
    ls_map = {0: "-", 2: "--", 4: ":"}
    for dc, bead in key_combos:
        for topo in ["uni", "bi"]:
            sub = df[(df["topology"] == topo) &
                     (df["defect_count"] == dc) &
                     (np.isclose(df["bead_height_mm"], bead))]
            sub = sub.sort_values("total_flow_lpm")
            label = f"{TOPO_LABELS[topo]}, dc={dc}, h={bead}"
            ax.plot(sub["total_flow_lpm"], sub["terminal_mpa"],
                    color=TOPO_COLORS[topo], ls=ls_map[dc],
                    marker=TOPO_MARKERS[topo], markersize=4,
                    label=label, linewidth=1.3)
    ax.axhline(PASS_LINE, color="gray", ls="--", lw=0.8)
    ax.set_xlabel("총 유량 (LPM)")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title("A-2: 유량 sweep 결정론 비교")
    ax.legend(fontsize=7, ncol=2, loc="upper right")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    save(fig, "A2_terminal_vs_flow.png")


# ═══════════════════════════════════════════════════════════
#  3. B1 — 임계 입구압력 vs 비드높이 × 결함개수
# ═══════════════════════════════════════════════════════════
def plot_B1():
    df = pd.read_csv(DATA_DIR / "B1_design_flow_critical_pressure.csv")
    defects = sorted(df["defect_count"].unique())
    fig, ax = plt.subplots(figsize=(8, 5))
    ls_map = {0: "-", 1: "--", 2: "-.", 3: ":", 4: (0, (3, 1, 1, 1))}
    for dc in defects:
        for topo in ["uni", "bi"]:
            sub = df[(df["topology"] == topo) & (df["defect_count"] == dc)]
            sub = sub.sort_values("bead_height_mm")
            ax.plot(sub["bead_height_mm"], sub["critical_inlet_mpa"],
                    color=TOPO_COLORS[topo], ls=ls_map.get(dc, "-"),
                    marker=TOPO_MARKERS[topo], markersize=4,
                    label=f"{topo}, dc={dc}", linewidth=1.3)
    ax.axhline(0.532723, color="green", ls="--", lw=1, label="P_REF (0.5327)")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("임계 입구압력 (MPa)")
    ax.set_title("B-1: 설계유량 임계압력 비교")
    ax.legend(fontsize=7, ncol=2, loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    save(fig, "B1_critical_pressure.png")


# ═══════════════════════════════════════════════════════════
#  4. B2 — 임계압력 히트맵
# ═══════════════════════════════════════════════════════════
def plot_B2():
    df = pd.read_csv(DATA_DIR / "B2_flow_bead_critical_map.csv")
    defects = sorted(df["defect_count"].unique())
    topos = ["uni", "bi"]
    fig, axes = plt.subplots(len(defects), len(topos),
                             figsize=(5 * len(topos), 3.5 * len(defects)),
                             squeeze=False)
    for i, dc in enumerate(defects):
        for j, topo in enumerate(topos):
            ax = axes[i][j]
            sub = df[(df["topology"] == topo) & (df["defect_count"] == dc)]
            if sub.empty:
                ax.set_visible(False)
                continue
            piv = sub.pivot_table(index="bead_height_mm", columns="total_flow_lpm",
                                  values="critical_inlet_mpa")
            im = ax.imshow(piv.values, aspect="auto", origin="lower",
                           cmap="YlOrRd",
                           extent=[piv.columns.min(), piv.columns.max(),
                                   piv.index.min(), piv.index.max()])
            ax.set_title(f"{TOPO_LABELS[topo]}, dc={dc}", fontsize=10)
            ax.set_xlabel("유량 (LPM)")
            ax.set_ylabel("비드 높이 (mm)")
            plt.colorbar(im, ax=ax, label="임계 P_in (MPa)")
    fig.suptitle("B-2: 유량-비드 임계압력 맵", fontsize=13, y=1.01)
    fig.tight_layout()
    save(fig, "B2_critical_pressure_map.png")


# ═══════════════════════════════════════════════════════════
#  5. C1 — S-curve: 실패율 vs 입구압력
# ═══════════════════════════════════════════════════════════
def plot_C1():
    df = pd.read_csv(DATA_DIR / "C1_reliability_pressure_transition.csv")
    # 대표 조건: bead_std=0.50, bead_mean 별, defect_count 별
    std_val = 0.50
    bead_means = sorted(df["bead_height_mm"].unique())
    defects = sorted(df["defect_count"].unique())

    fig, axes = plt.subplots(1, len(bead_means),
                             figsize=(4 * len(bead_means), 4.5), sharey=True)
    if len(bead_means) == 1:
        axes = [axes]
    ls_map = {1: "-", 2: "--", 3: "-.", 4: ":"}

    for ax, bm in zip(axes, bead_means):
        for dc in defects:
            for topo in ["uni", "bi"]:
                sub = df[(df["topology"] == topo) &
                         (np.isclose(df["bead_height_mm"], bm)) &
                         (np.isclose(df["bead_height_std_mm"], std_val)) &
                         (df["defect_count"] == dc)]
                if sub.empty:
                    continue
                sub = sub.sort_values("inlet_pressure_mpa")
                label = f"{topo}/dc={dc}"
                ax.plot(sub["inlet_pressure_mpa"], sub["fail_rate"],
                        color=TOPO_COLORS[topo], ls=ls_map.get(dc, "-"),
                        linewidth=1.3, label=label)
                ax.fill_between(sub["inlet_pressure_mpa"],
                                sub["fail_rate_CI95_low"],
                                sub["fail_rate_CI95_high"],
                                color=TOPO_COLORS[topo], alpha=0.08)
        ax.axvline(0.532723, color="green", ls="--", lw=0.8, alpha=0.7)
        ax.set_title(f"비드 μ={bm} mm, σ=0.50", fontsize=10)
        ax.set_xlabel("입구압력 (MPa)")
        if ax == axes[0]:
            ax.set_ylabel("실패율")
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-0.02, 1.02)
    axes[-1].legend(fontsize=6, ncol=2, loc="upper right")
    fig.suptitle("C-1: 입구압 전이구간 S-curve (σ=0.50 mm)", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "C1_scurve_pressure_std050.png")

    # 추가: std별 비교 (bead=1.5, defect=2)
    bm_fix, dc_fix = 1.5, 2
    stds = sorted(df["bead_height_std_mm"].unique())
    fig2, ax2 = plt.subplots(figsize=(7, 5))
    ls_std = {0.25: "-", 0.50: "--", 0.75: ":"}
    for std in stds:
        for topo in ["uni", "bi"]:
            sub = df[(df["topology"] == topo) &
                     (np.isclose(df["bead_height_mm"], bm_fix)) &
                     (np.isclose(df["bead_height_std_mm"], std)) &
                     (df["defect_count"] == dc_fix)]
            if sub.empty:
                continue
            sub = sub.sort_values("inlet_pressure_mpa")
            ax2.plot(sub["inlet_pressure_mpa"], sub["fail_rate"],
                     color=TOPO_COLORS[topo], ls=ls_std.get(std, "-"),
                     linewidth=1.3, label=f"{topo}/σ={std}")
            ax2.fill_between(sub["inlet_pressure_mpa"],
                             sub["fail_rate_CI95_low"],
                             sub["fail_rate_CI95_high"],
                             color=TOPO_COLORS[topo], alpha=0.06)
    ax2.axvline(0.532723, color="green", ls="--", lw=0.8)
    ax2.set_xlabel("입구압력 (MPa)")
    ax2.set_ylabel("실패율")
    ax2.set_title(f"C-1: σ 민감도 (μ={bm_fix}, dc={dc_fix})")
    ax2.legend(fontsize=8, ncol=2)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(-0.02, 1.02)
    fig2.tight_layout()
    save(fig2, "C1_scurve_std_sensitivity.png")


# ═══════════════════════════════════════════════════════════
#  6. C2 — 기준선
# ═══════════════════════════════════════════════════════════
def plot_C2():
    df = pd.read_csv(DATA_DIR / "C2_baseline_pressure_transition.csv")
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for topo in ["uni", "bi"]:
        sub = df[df["topology"] == topo].sort_values("inlet_pressure_mpa")
        ax.plot(sub["inlet_pressure_mpa"], sub["terminal_mpa"],
                color=TOPO_COLORS[topo], marker=TOPO_MARKERS[topo],
                label=TOPO_LABELS[topo], linewidth=1.5, markersize=5)
    ax.axhline(PASS_LINE, color="gray", ls="--", lw=0.8, label="PASS 기준")
    ax.axvline(0.532723, color="green", ls="--", lw=0.8, label="P_REF")
    ax.set_xlabel("입구압력 (MPa)")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title("C-2: 기준선 (결함 없음)")
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    save(fig, "C2_baseline.png")


# ═══════════════════════════════════════════════════════════
#  7. D1 — S-curve: 실패율 vs 유량
# ═══════════════════════════════════════════════════════════
def plot_D1():
    df = pd.read_csv(DATA_DIR / "D1_reliability_flow_transition.csv")
    std_val = 0.50
    bead_means = sorted(df["bead_height_mm"].unique())
    defects = sorted(df["defect_count"].unique())

    fig, axes = plt.subplots(1, len(bead_means),
                             figsize=(4 * len(bead_means), 4.5), sharey=True)
    if len(bead_means) == 1:
        axes = [axes]
    ls_map = {1: "-", 2: "--", 3: "-.", 4: ":"}

    for ax, bm in zip(axes, bead_means):
        for dc in defects:
            for topo in ["uni", "bi"]:
                sub = df[(df["topology"] == topo) &
                         (np.isclose(df["bead_height_mm"], bm)) &
                         (np.isclose(df["bead_height_std_mm"], std_val)) &
                         (df["defect_count"] == dc)]
                if sub.empty:
                    continue
                sub = sub.sort_values("total_flow_lpm")
                ax.plot(sub["total_flow_lpm"], sub["fail_rate"],
                        color=TOPO_COLORS[topo], ls=ls_map.get(dc, "-"),
                        linewidth=1.3, label=f"{topo}/dc={dc}")
                ax.fill_between(sub["total_flow_lpm"],
                                sub["fail_rate_CI95_low"],
                                sub["fail_rate_CI95_high"],
                                color=TOPO_COLORS[topo], alpha=0.08)
        ax.set_title(f"비드 μ={bm} mm, σ=0.50", fontsize=10)
        ax.set_xlabel("유량 (LPM)")
        if ax == axes[0]:
            ax.set_ylabel("실패율")
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-0.02, 1.02)
    axes[-1].legend(fontsize=6, ncol=2, loc="lower right")
    fig.suptitle("D-1: 유량 전이구간 S-curve (σ=0.50 mm)", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "D1_scurve_flow_std050.png")


# ═══════════════════════════════════════════════════════════
#  8. E1 — 위치 민감도
# ═══════════════════════════════════════════════════════════
def plot_E1():
    df = pd.read_csv(DATA_DIR / "E1_position_sensitivity.csv")
    beads = sorted(df["bead_height_mm"].unique())
    fig, axes = plt.subplots(1, len(beads), figsize=(4.5 * len(beads), 4.5),
                             sharey=True)
    if len(beads) == 1:
        axes = [axes]
    bar_w = 0.35
    for ax, bm in zip(axes, beads):
        for k, topo in enumerate(["uni", "bi"]):
            sub = df[(df["topology"] == topo) &
                     (np.isclose(df["bead_height_mm"], bm))]
            sub = sub.sort_values("head_position")
            positions = sub["head_position"].values
            x = np.arange(len(positions))
            ax.bar(x + k * bar_w, sub["delta_mpa"].values * 1000,  # kPa로 변환
                   bar_w, color=TOPO_COLORS[topo], alpha=0.8,
                   label=TOPO_LABELS[topo])
        ax.set_title(f"비드 {bm} mm", fontsize=11)
        ax.set_xlabel("헤드 위치 (0=입구, 7=말단)")
        ax.set_xticks(np.arange(len(positions)) + bar_w / 2)
        ax.set_xticklabels([str(int(p)) for p in positions])
        if ax == axes[0]:
            ax.set_ylabel("압력 감소량 (kPa)")
        ax.grid(True, alpha=0.3, axis="y")
    axes[0].legend(fontsize=9)
    fig.suptitle("E-1: 결함 위치별 말단 압력 감소량", fontsize=13, y=1.02)
    fig.tight_layout()
    save(fig, "E1_position_sensitivity.png")


# ═══════════════════════════════════════════════════════════
#  손실 분해 스택 차트 (A1 데이터)
# ═══════════════════════════════════════════════════════════
def plot_loss_breakdown():
    df = pd.read_csv(DATA_DIR / "A1_design_flow_deterministic.csv")
    # defect=0, bead=0 조건에서 uni vs bi 손실 분해
    loss_cols = ["loss_pipe_mpa", "loss_fitting_mpa", "loss_bead_mpa",
                 "cross_main_loss_mpa", "tee_split_loss_mpa", "equipment_loss_mpa"]
    loss_labels = ["배관 마찰", "이음쇠", "용접 비드", "교차배관", "T분기", "장비"]
    colors = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948"]

    fig, axes = plt.subplots(1, 2, figsize=(10, 5))
    for ax, dc in zip(axes, [0, 4]):
        bottom_uni = 0
        bottom_bi = 0
        x = np.array([0, 1])
        for col, lbl, clr in zip(loss_cols, loss_labels, colors):
            uni_val = df[(df["topology"] == "uni") & (df["defect_count"] == dc) &
                         (np.isclose(df["bead_height_mm"], 0.0 if dc == 0 else 1.5))][col].values
            bi_val = df[(df["topology"] == "bi") & (df["defect_count"] == dc) &
                        (np.isclose(df["bead_height_mm"], 0.0 if dc == 0 else 1.5))][col].values
            if len(uni_val) == 0 or len(bi_val) == 0:
                continue
            vals = [uni_val[0], bi_val[0]]
            ax.bar(x, vals, 0.5, bottom=[bottom_uni, bottom_bi],
                   label=lbl, color=clr, edgecolor="white", linewidth=0.5)
            bottom_uni += vals[0]
            bottom_bi += vals[1]
        bead_txt = "0.0" if dc == 0 else "1.5"
        ax.set_title(f"결함 {dc}개, 비드 {bead_txt} mm", fontsize=11)
        ax.set_xticks(x)
        ax.set_xticklabels(["단방향", "양방향"])
        ax.set_ylabel("압력 손실 (MPa)")
        ax.legend(fontsize=7, loc="upper right")
    fig.suptitle("손실 분해 비교", fontsize=13)
    fig.tight_layout()
    save(fig, "A1_loss_breakdown.png")


# ═══════════════════════════════════════════════════════════
#  main
# ═══════════════════════════════════════════════════════════
ALL_PLOTS = {
    "A1": plot_A1, "A2": plot_A2,
    "B1": plot_B1, "B2": plot_B2,
    "C1": plot_C1, "C2": plot_C2,
    "D1": plot_D1, "E1": plot_E1,
    "LOSS": plot_loss_breakdown,
}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plot", default="ALL",
                        help="A1|A2|B1|B2|C1|C2|D1|E1|LOSS|ALL")
    parser.add_argument("--datadir", default=None,
                        help="데이터/그래프 기본 디렉토리 (기본: uni_bi_simulation_results)")
    args = parser.parse_args()

    if args.datadir:
        DATA_DIR = Path(args.datadir) / "data"
        FIG_DIR = Path(args.datadir) / "figures"
        FIG_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  uni_bi_simulation_results 그래프 생성")
    print("=" * 60)

    if args.plot == "ALL":
        targets = ALL_PLOTS
    else:
        targets = {k: v for k, v in ALL_PLOTS.items() if k == args.plot.upper()}
        if not targets:
            print(f"  [에러] 알 수 없는 플롯: {args.plot}")
            sys.exit(1)

    for name, fn in targets.items():
        print(f"\n  [{name}] 생성 중...")
        try:
            fn()
        except Exception as e:
            print(f"  [에러] {name}: {e}")

    print(f"\n  완료! 그래프: {FIG_DIR}/")
