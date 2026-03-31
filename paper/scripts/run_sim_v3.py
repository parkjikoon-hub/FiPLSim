#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim 논문용 시뮬레이션 V3 — 3rd 배치 (전이구간 보강)
==========================================================================
목적: 2nd 시뮬레이션에서 P_REF 기준 Pf=100%로 의미 있는 전이곡선이 안 나온
      S2, P1, D1을 전이구간 입구압에서 재실행.

케이스:
  S2b — 비드 개수 × 입구압 전이구간 (defect_count vs Pf)
  P1b — 확률적 전이구간 확대 (Bernoulli MC, 입구압 5포인트)
  D1b — 31/32 헤드 비교 (결정론적)

실행:
  PYTHONIOENCODING=utf-8 python3 run_sim_v3.py              # 전체 실행
  PYTHONIOENCODING=utf-8 python3 run_sim_v3.py --case S2b   # 개별 케이스
  PYTHONIOENCODING=utf-8 python3 run_sim_v3.py --case P1b
  PYTHONIOENCODING=utf-8 python3 run_sim_v3.py --case D1b
"""

import sys
import os
import argparse
import json
import time
import importlib
import math
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# ── 프로젝트 루트를 sys.path에 추가 (paper/scripts/ → 루트) ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ──────────────────────────────────────────────
# 논문용 물성치 오버라이드
# ──────────────────────────────────────────────
import constants
constants.EPSILON_MM = 0.046
constants.EPSILON_M = 0.046 / 1000.0
constants.RHO = 1000.0
constants.MU = 1.002e-3
constants.NU = constants.MU / constants.RHO

import hydraulics
importlib.reload(hydraulics)

import pipe_network as pn
importlib.reload(pn)

import simulation as sim
importlib.reload(sim)

# matplotlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

_KOREAN_FONTS = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "AppleGothic"]
_font_set = False
for _fname in _KOREAN_FONTS:
    _flist = [f for f in fm.fontManager.ttflist if _fname in f.name]
    if _flist:
        plt.rcParams["font.family"] = _fname
        plt.rcParams["axes.unicode_minus"] = False
        _font_set = True
        break
if not _font_set:
    warnings.warn("한글 폰트를 찾을 수 없습니다.")

# ──────────────────────────────────────────────
# 고정 구조 파라미터 (2nd와 동일)
# ──────────────────────────────────────────────
NUM_BRANCHES = 4
HEADS_PER_BRANCH = 8
ACTIVE_HEADS = 32
TOTAL_FLOW_LPM = 2560.0
BRANCH_SPACING_M = 3.5
HEAD_SPACING_M = 2.1
BRANCH_INLET_CONFIG = "80A-65A"
SUPPLY_PIPE_SIZE = "100A"
USE_HEAD_FITTING = True
REDUCER_MODE = "crane"
P_REF = 0.5314
PASS_THRESHOLD_MPA = 0.1

K1_BASE = constants.K1_BASE
K2 = constants.K2
K3 = constants.K3

EQUIPMENT_K_FACTORS = {
    "알람밸브 (습식)":     {"K": 2.0,  "qty": 1},
    "유수검지장치":        {"K": 1.0,  "qty": 1},
    "게이트밸브 (전개)":   {"K": 0.15, "qty": 2},
    "체크밸브 (스윙형)":   {"K": 2.0,  "qty": 1},
    "90° 엘보":           {"K": 0.75, "qty": 1},
    "리듀서 (점축소)":     {"K": 0.15, "qty": 1},
}

# 출력 디렉토리 (3rd sim_results)
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "3rd sim_results"
DATA_DIR = OUTPUT_DIR / "data"
FIG_DIR = OUTPUT_DIR / "figures"


# ══════════════════════════════════════════════
#  공통 유틸리티
# ══════════════════════════════════════════════

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)


def save_csv(df, path):
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  -> CSV: {path.name}")


def save_fig(fig, path, dpi=200):
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  -> PNG: {path.name}")


def elapsed(t0):
    return f"{time.time() - t0:.1f}s"


def common_params():
    """compare_dynamic_cases 등 고수준 함수용"""
    return dict(
        num_branches=NUM_BRANCHES,
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        equipment_k_factors=EQUIPMENT_K_FACTORS,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
        branch_inlet_config=BRANCH_INLET_CONFIG,
    )


def common_params_mc():
    """MC/sensitivity 함수용"""
    return dict(
        **common_params(),
        K1_base=K1_BASE,
        K2_val=K2,
        K3_val=K3,
    )


def make_row(case_id, bead_height_mm, **extras):
    """표준 CSV 행 생성"""
    row = {
        "case_id": case_id,
        "topology": extras.get("topology", "tree"),
        "branch_inlet_config": extras.get("branch_inlet_config", BRANCH_INLET_CONFIG),
        "num_branches": NUM_BRANCHES,
        "heads_per_branch": HEADS_PER_BRANCH,
        "active_heads": extras.get("active_heads", ACTIVE_HEADS),
        "total_flow_lpm": extras.get("total_flow_lpm", TOTAL_FLOW_LPM),
        "inlet_pressure_mpa": extras.get("inlet_pressure_mpa", P_REF),
        "bead_height_mm": bead_height_mm,
        "bead_height_std_mm": extras.get("bead_height_std_mm", None),
        "defect_count": extras.get("defect_count", None),
        "p_bead": extras.get("p_bead", None),
        "mc_iterations": extras.get("mc_iterations", None),
        "terminal_pressure_mpa": extras.get("terminal_pressure_mpa", None),
        "pressure_margin_mpa": extras.get("pressure_margin_mpa", None),
        "pass_fail": extras.get("pass_fail", None),
        "loss_pipe_mpa": extras.get("loss_pipe_mpa", None),
        "loss_fitting_mpa": extras.get("loss_fitting_mpa", None),
        "loss_bead_mpa": extras.get("loss_bead_mpa", None),
        "required_extra_inlet_pressure_mpa": extras.get("required_extra_inlet_pressure_mpa", None),
        "worst_branch_index": extras.get("worst_branch_index", None),
        "mean_terminal_mpa": extras.get("mean_terminal_mpa", None),
        "std_terminal_mpa": extras.get("std_terminal_mpa", None),
        "P5_terminal_mpa": extras.get("P5_terminal_mpa", None),
        "P50_terminal_mpa": extras.get("P50_terminal_mpa", None),
        "P95_terminal_mpa": extras.get("P95_terminal_mpa", None),
        "fail_rate": extras.get("fail_rate", None),
        "fail_rate_CI95_low": extras.get("fail_rate_CI95_low", None),
        "fail_rate_CI95_high": extras.get("fail_rate_CI95_high", None),
    }
    for k, v in extras.items():
        if k not in row:
            row[k] = v
    return row


def ci95_proportion(p, n):
    """비율의 95% CI (Wilson 근사)"""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denominator
    margin = z * math.sqrt((p*(1-p) + z**2/(4*n)) / n) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def calc_required_extra_pressure(terminal_mpa):
    """0.1 MPa 미달 시 추가 필요 입구압"""
    if terminal_mpa < PASS_THRESHOLD_MPA:
        return PASS_THRESHOLD_MPA - terminal_mpa
    return 0.0


# ══════════════════════════════════════════════
#  S2b: 비드 개수 × 입구압 전이구간
# ══════════════════════════════════════════════

def run_S2b():
    print("\n" + "="*60)
    print("  S2b: 비드 개수 × 입구압 전이구간 (32헤드)")
    print("="*60)
    t0 = time.time()

    inlet_pressures = [0.55, 0.56, 0.58, 0.60]
    bead_heights = [1.5, 2.0, 2.5, 3.0]
    defect_counts = [0, 1, 2, 4, 8]
    mc_iter = 10000
    Q = TOTAL_FLOW_LPM

    total_runs = len(inlet_pressures) * len(bead_heights) * len(defect_counts)
    run_count = 0

    rows = []
    for p_in in inlet_pressures:
        for bh in bead_heights:
            bh_std = bh * 0.5
            for dc in defect_counts:
                run_count += 1
                print(f"    [{run_count}/{total_runs}] P_in={p_in} bead={bh}mm(+/-{bh_std:.1f}) dc={dc} ...",
                      end=" ", flush=True)

                if dc == 0:
                    # 결정론적: 비드 없음
                    result = pn.compare_dynamic_cases(
                        bead_height_existing=0.0,
                        bead_height_new=0.0,
                        inlet_pressure_mpa=p_in,
                        total_flow_lpm=Q,
                        K1_base=K1_BASE, K2_val=K2, K3_val=K3,
                        use_head_fitting=USE_HEAD_FITTING,
                        reducer_mode=REDUCER_MODE,
                        **common_params(),
                    )
                    term = result["terminal_B_mpa"]
                    row = make_row("S2b", 0.0,
                        inlet_pressure_mpa=p_in,
                        bead_height_std_mm=0.0,
                        defect_count=0,
                        mc_iterations=0,
                        terminal_pressure_mpa=term,
                        mean_terminal_mpa=term,
                        std_terminal_mpa=0.0,
                        fail_rate=0.0 if term >= PASS_THRESHOLD_MPA else 1.0,
                    )
                    rows.append(row)
                    print(f"OK (deterministic, term={term:.4f})")
                else:
                    mc_result = sim.run_dynamic_monte_carlo(
                        n_iterations=mc_iter,
                        min_defects=dc,
                        max_defects=dc,
                        bead_height_mm=bh,
                        bead_height_std_mm=bh_std,
                        total_flow_lpm=Q,
                        inlet_pressure_mpa=p_in,
                        use_head_fitting=USE_HEAD_FITTING,
                        reducer_mode=REDUCER_MODE,
                        **common_params_mc(),
                    )
                    pressures = mc_result["terminal_pressures"]
                    pf = mc_result["p_below_threshold"]
                    ci_low, ci_high = ci95_proportion(pf, mc_iter)

                    row = make_row("S2b", bh,
                        inlet_pressure_mpa=p_in,
                        bead_height_std_mm=bh_std,
                        defect_count=dc,
                        mc_iterations=mc_iter,
                        mean_terminal_mpa=mc_result["mean_pressure"],
                        std_terminal_mpa=mc_result["std_pressure"],
                        P5_terminal_mpa=float(np.percentile(pressures, 5)),
                        P50_terminal_mpa=float(np.percentile(pressures, 50)),
                        P95_terminal_mpa=float(np.percentile(pressures, 95)),
                        fail_rate=pf,
                        fail_rate_CI95_low=ci_low,
                        fail_rate_CI95_high=ci_high,
                    )
                    rows.append(row)
                    print(f"Pf={pf*100:.2f}%")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "S2b_defect_count_transition.csv")

    # ── 그래프 1: 입구압별 defect_count vs fail_rate ──
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()
    cmap = plt.cm.Reds(np.linspace(0.3, 0.95, len(bead_heights)))

    for ax_idx, p_in in enumerate(inlet_pressures):
        ax = axes[ax_idx]
        sub_p = df[df["inlet_pressure_mpa"] == p_in]
        for i, bh in enumerate(bead_heights):
            sub = sub_p[(sub_p["bead_height_mm"] == bh) & (sub_p["defect_count"] > 0)]
            if len(sub) == 0:
                continue
            ax.plot(sub["defect_count"].values, sub["fail_rate"].values * 100,
                    marker="o", color=cmap[i], markersize=6,
                    label=f"bead={bh}mm (+/-{bh*0.5:.1f})")
        # dc=0 기준선
        dc0 = sub_p[sub_p["defect_count"] == 0]
        if len(dc0) > 0:
            pf0 = dc0["fail_rate"].values[0] * 100
            ax.axhline(y=pf0, color="blue", linestyle=":", alpha=0.5,
                       label=f"비드없음 Pf={pf0:.0f}%")
        ax.set_xlabel("비드 개수 (defect_count)")
        ax.set_ylabel("실패율 (%)")
        ax.set_title(f"P_in={p_in} MPa")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-5, 105)

    fig.suptitle("S2b: 비드개수별 실패율 — 전이구간 (32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "S2b_defect_vs_fail_transition.png")

    # ── 그래프 2: 입구압별 defect_count vs mean_terminal ──
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()

    for ax_idx, p_in in enumerate(inlet_pressures):
        ax = axes[ax_idx]
        sub_p = df[df["inlet_pressure_mpa"] == p_in]
        for i, bh in enumerate(bead_heights):
            sub = sub_p[(sub_p["bead_height_mm"] == bh) & (sub_p["defect_count"] > 0)]
            if len(sub) == 0:
                continue
            means = sub["mean_terminal_mpa"].values
            stds = sub["std_terminal_mpa"].values
            ax.errorbar(sub["defect_count"].values, means, yerr=1.96*stds,
                        marker="o", color=cmap[i], capsize=4, markersize=6,
                        label=f"bead={bh}mm (+/-{bh*0.5:.1f})")
        dc0 = sub_p[sub_p["defect_count"] == 0]
        if len(dc0) > 0:
            ax.axhline(y=dc0["mean_terminal_mpa"].values[0], color="blue",
                       linestyle=":", alpha=0.5, label="비드없음")
        ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--",
                   alpha=0.7, label="0.1 MPa 규정선")
        ax.set_xlabel("비드 개수 (defect_count)")
        ax.set_ylabel("평균 말단 압력 (MPa)")
        ax.set_title(f"P_in={p_in} MPa")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    fig.suptitle("S2b: 비드개수별 평균말단압 — 전이구간 (32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "S2b_defect_vs_terminal_transition.png")

    print(f"  S2b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  P1b: 확률적 전이구간 확대
# ══════════════════════════════════════════════

def run_P1b():
    print("\n" + "="*60)
    print("  P1b: 확률적 전이구간 확대 (32헤드, 입구압 5포인트)")
    print("="*60)
    t0 = time.time()

    inlet_pressures = [0.54, 0.55, 0.56, 0.58, 0.60]
    p_bead_list = [0.05, 0.10, 0.20, 0.30]
    mu_h_list = [1.0, 1.5, 2.0, 2.5, 3.0]
    mc_iter = 10000
    Q = TOTAL_FLOW_LPM

    total_runs = len(inlet_pressures) * len(p_bead_list) * len(mu_h_list)
    run_count = 0

    rows = []
    for p_in in inlet_pressures:
        for pb in p_bead_list:
            for mu in mu_h_list:
                sig = mu * 0.5
                run_count += 1
                print(f"    [{run_count}/{total_runs}] P_in={p_in} p={pb} mu={mu} sig={sig:.2f} ...",
                      end=" ", flush=True)

                mc_result = sim.run_bernoulli_monte_carlo(
                    p_bead=pb,
                    n_iterations=mc_iter,
                    bead_height_mm=mu,
                    bead_height_std_mm=sig,
                    total_flow_lpm=Q,
                    inlet_pressure_mpa=p_in,
                    use_head_fitting=USE_HEAD_FITTING,
                    reducer_mode=REDUCER_MODE,
                    **common_params_mc(),
                )

                pressures = mc_result["terminal_pressures"]
                pf = mc_result["p_below_threshold"]
                ci_low, ci_high = ci95_proportion(pf, mc_iter)

                row = make_row("P1b", mu,
                    inlet_pressure_mpa=p_in,
                    bead_height_std_mm=sig,
                    p_bead=pb,
                    mc_iterations=mc_iter,
                    mean_terminal_mpa=mc_result["mean_pressure"],
                    std_terminal_mpa=mc_result["std_pressure"],
                    P5_terminal_mpa=float(np.percentile(pressures, 5)),
                    P50_terminal_mpa=float(np.percentile(pressures, 50)),
                    P95_terminal_mpa=float(np.percentile(pressures, 95)),
                    fail_rate=pf,
                    fail_rate_CI95_low=ci_low,
                    fail_rate_CI95_high=ci_high,
                )
                rows.append(row)
                print(f"Pf={pf*100:.2f}%")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "P1b_probabilistic_transition.csv")

    # ── 그래프 1: 히트맵 서브플롯 (입구압별) ──
    fig, axes = plt.subplots(1, len(inlet_pressures), figsize=(5*len(inlet_pressures), 5))
    if len(inlet_pressures) == 1:
        axes = [axes]

    for ax_idx, p_in in enumerate(inlet_pressures):
        ax = axes[ax_idx]
        sub = df[df["inlet_pressure_mpa"] == p_in]
        matrix = np.zeros((len(mu_h_list), len(p_bead_list)))
        for i, mu in enumerate(mu_h_list):
            for j, pb in enumerate(p_bead_list):
                val = sub[(sub["bead_height_mm"] == mu) & (sub["p_bead"] == pb)]["fail_rate"]
                matrix[i, j] = val.values[0] * 100 if len(val) > 0 else 0

        im = ax.imshow(matrix, cmap="Reds", aspect="auto", vmin=0, vmax=100)
        ax.set_xticks(range(len(p_bead_list)))
        ax.set_xticklabels([f"{p}" for p in p_bead_list], fontsize=8)
        ax.set_yticks(range(len(mu_h_list)))
        ax.set_yticklabels([f"{m}mm" for m in mu_h_list], fontsize=8)
        ax.set_xlabel("p_bead")
        ax.set_ylabel("mu_h (mm)")
        ax.set_title(f"P_in={p_in}", fontsize=10)
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                ax.text(j, i, f"{matrix[i,j]:.1f}%", ha="center", va="center", fontsize=7)

    fig.suptitle("P1b: p_bead x mu_h 실패율 히트맵 (전이구간, 32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "P1b_heatmap_transition.png")

    # ── 그래프 2: 입구압 vs Pf 곡선 (p_bead별 × mu_h별) ──
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()
    cmap2 = plt.cm.Reds(np.linspace(0.2, 0.9, len(mu_h_list)))

    for ax_idx, pb in enumerate(p_bead_list):
        ax = axes[ax_idx]
        sub = df[df["p_bead"] == pb]
        for i, mu in enumerate(mu_h_list):
            s = sub[sub["bead_height_mm"] == mu].sort_values("inlet_pressure_mpa")
            ax.plot(s["inlet_pressure_mpa"], s["fail_rate"] * 100,
                    marker="o", color=cmap2[i], markersize=6,
                    label=f"mu={mu}mm (sig={mu*0.5:.1f})")
        ax.set_xlabel("입구 압력 (MPa)")
        ax.set_ylabel("실패율 (%)")
        ax.set_title(f"p_bead={pb}")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-5, 105)

    fig.suptitle("P1b: 입구압별 실패율 — 전이구간 (32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "P1b_inlet_vs_pf_transition.png")

    # ── 그래프 3: mu_h별 fail_rate 바 차트 (입구압별 패널) ──
    fig, axes = plt.subplots(1, len(inlet_pressures), figsize=(5*len(inlet_pressures), 5))
    if len(inlet_pressures) == 1:
        axes = [axes]
    pb_colors = {0.05: "#3498db", 0.10: "#2ecc71", 0.20: "#e67e22", 0.30: "#e74c3c"}

    for ax_idx, p_in in enumerate(inlet_pressures):
        ax = axes[ax_idx]
        sub = df[df["inlet_pressure_mpa"] == p_in]
        x = np.arange(len(mu_h_list))
        w = 0.2
        for i_pb, pb in enumerate(p_bead_list):
            vals = []
            for mu in mu_h_list:
                v = sub[(sub["bead_height_mm"] == mu) & (sub["p_bead"] == pb)]["fail_rate"]
                vals.append(v.values[0] * 100 if len(v) > 0 else 0)
            offset = (i_pb - len(p_bead_list)/2 + 0.5) * w
            ax.bar(x + offset, vals, w, label=f"p={pb}", color=pb_colors[pb], alpha=0.8)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{m}mm" for m in mu_h_list], fontsize=8)
        ax.set_xlabel("mu_h (mm)")
        ax.set_ylabel("실패율 (%)")
        ax.set_title(f"P_in={p_in}", fontsize=10)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3, axis="y")

    fig.suptitle("P1b: mu_h별 실패율 바 차트 (전이구간, 32헤드)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "P1b_mu_vs_failrate_transition.png")

    print(f"  P1b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  D1b: 31/32 헤드 비교 (결정론적)
# ══════════════════════════════════════════════

def run_D1b():
    print("\n" + "="*60)
    print("  D1b: 31/32 헤드 비교 (결정론적, P_in=P_REF)")
    print("="*60)
    t0 = time.time()

    heads_list = [31, 32]
    bead_heights = [0, 1.5, 2.5, 3.0]

    rows = []
    for ah in heads_list:
        flow = ah * 80.0  # 31헤드→2480, 32헤드→2560 LPM
        for bh in bead_heights:
            print(f"    heads={ah} bead={bh}mm Q={flow} LPM ...", end=" ", flush=True)

            result = pn.compare_dynamic_cases(
                bead_height_existing=bh,
                bead_height_new=0.0,
                inlet_pressure_mpa=P_REF,
                total_flow_lpm=flow,
                K1_base=K1_BASE, K2_val=K2, K3_val=K3,
                use_head_fitting=USE_HEAD_FITTING,
                reducer_mode=REDUCER_MODE,
                **common_params(),
            )

            term_a = result["terminal_A_mpa"]
            loss_bead = result["system_A"]["loss_bead_mpa"]

            row = make_row("D1b", bh,
                active_heads=ah,
                total_flow_lpm=flow,
                inlet_pressure_mpa=P_REF,
                terminal_pressure_mpa=term_a,
                pressure_margin_mpa=term_a - PASS_THRESHOLD_MPA,
                pass_fail=result["pass_fail_A"],
                loss_pipe_mpa=result["system_A"]["loss_pipe_mpa"],
                loss_fitting_mpa=result["system_A"]["loss_fitting_mpa"],
                loss_bead_mpa=loss_bead,
                required_extra_inlet_pressure_mpa=calc_required_extra_pressure(term_a),
                worst_branch_index=result["worst_branch_A"],
                terminal_B_mpa=result["terminal_B_mpa"],
                improvement_pct=result["improvement_pct"],
            )
            rows.append(row)
            print(f"term={term_a:.4f} MPa, {'PASS' if term_a >= PASS_THRESHOLD_MPA else 'FAIL'}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "D1b_heads_transition.csv")

    # ── 그래프: active_heads vs terminal_pressure ──
    fig, ax = plt.subplots(figsize=(8, 6))
    cmap = plt.cm.Reds(np.linspace(0.2, 0.9, len(bead_heights)))
    for i, bh in enumerate(bead_heights):
        sub = df[df["bead_height_mm"] == bh].sort_values("active_heads")
        ax.plot(sub["active_heads"], sub["terminal_pressure_mpa"],
                marker="o", color=cmap[i], markersize=10, linewidth=2,
                label=f"bead={bh}mm")
        # 각 점에 값 표시
        for _, r in sub.iterrows():
            ax.annotate(f"{r['terminal_pressure_mpa']:.4f}",
                       (r["active_heads"], r["terminal_pressure_mpa"]),
                       textcoords="offset points", xytext=(10, 5), fontsize=8)

    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", linewidth=1.5,
               alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xlabel("활성 헤드 수")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title(f"D1b: 31/32 헤드 전이 (P_in={P_REF}, 비드높이별)")
    ax.set_xticks(heads_list)
    ax.legend()
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1b_heads_vs_terminal.png")

    print(f"  D1b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  메인 진입점
# ══════════════════════════════════════════════

ALL_CASES = {
    "S2B": run_S2b,
    "P1B": run_P1b,
    "D1B": run_D1b,
}


def main():
    parser = argparse.ArgumentParser(description="FiPLSim 논문용 3rd 시뮬레이션 (전이구간)")
    parser.add_argument("--case", type=str, help="실행할 케이스 (S2b, P1b, D1b)")
    args = parser.parse_args()

    ensure_dirs()

    print("=" * 60)
    print("  FiPLSim 논문용 시뮬레이션 V3 (3rd: 전이구간 보강)")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  헤드: {ACTIVE_HEADS}개, 유량: {TOTAL_FLOW_LPM} LPM")
    print(f"  P_REF = {P_REF} MPa")
    print(f"  물성치: rho={constants.RHO}, eps={constants.EPSILON_MM}mm, nu={constants.NU:.6e}")
    print("=" * 60)

    t_total = time.time()

    if args.case:
        case = args.case.upper()
        if case in ALL_CASES:
            ALL_CASES[case]()
        else:
            print(f"  오류: '{case}'는 유효한 케이스가 아닙니다.")
            print(f"  사용 가능: {', '.join(ALL_CASES.keys())}")
            sys.exit(1)
    else:
        # 전체 실행: D1b(빠름) → S2b → P1b(느림)
        run_D1b()
        run_S2b()
        run_P1b()

    print(f"\n총 실행 시간: {elapsed(t_total)}")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print("완료!")


if __name__ == "__main__":
    main()
