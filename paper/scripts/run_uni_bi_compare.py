#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim: 단방향(4가지) vs 양방향(2+2가지) 배관 비교 시뮬레이션
================================================================
단방향: 입구 ──80A── B#0 ─ B#1 ─ B#2 ─ B#3  (4가지, 교차배관 3구간)
양방향: B#1 ─ B#0 ──80A── 입구 ──80A── B#0 ─ B#1  (좌2+우2, 1구간/측)

캠페인:
  A — 결정론적 비교 (유량 × 비드높이 스윕)
  B — 임계 입구압 탐색 (비드높이별)
  C — MC 실패율 비교 (유량 × 비드높이, σ=50%)

실행:
  PYTHONIOENCODING=utf-8 python3 run_uni_bi_compare.py
  PYTHONIOENCODING=utf-8 python3 run_uni_bi_compare.py --case A
"""

import sys
import os
import argparse
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
for _fname in _KOREAN_FONTS:
    if any(_fname in f.name for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _fname
        plt.rcParams["axes.unicode_minus"] = False
        break

# ──────────────────────────────────────────────
# 고정 파라미터
# ──────────────────────────────────────────────
HEADS_PER_BRANCH = 8
BRANCH_SPACING_M = 3.5
HEAD_SPACING_M = 2.1
BRANCH_INLET_CONFIG = "80A-65A"
SUPPLY_PIPE_SIZE = "100A"
USE_HEAD_FITTING = True
REDUCER_MODE = "crane"
PASS_THRESHOLD_MPA = 0.1
P_REF = 0.532723  # 통일 물성치 기준 (단방향 32헤드 bead-free 임계압)

K1_BASE = constants.K1_BASE
K2 = constants.K2
K3 = constants.K3
K_TEE_BRANCH = constants.K_TEE_BRANCH_80A  # 양방향 T분기 (NFPA 13, 80A 기준 K=1.06)

EQUIPMENT_K_FACTORS = {
    "알람밸브 (습식)":     {"K": 2.0,  "qty": 1},
    "유수검지장치":        {"K": 1.0,  "qty": 1},
    "게이트밸브 (전개)":   {"K": 0.15, "qty": 2},
    "체크밸브 (스윙형)":   {"K": 2.0,  "qty": 1},
    "90° 엘보":           {"K": 0.75, "qty": 1},
    "리듀서 (점축소)":     {"K": 0.15, "qty": 1},
}

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "uni_bi_branch_sim_compare"
DATA_DIR = OUTPUT_DIR / "data"
FIG_DIR = OUTPUT_DIR / "figures"


# ══════════════════════════════════════════════
#  유틸리티
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

def ci95_proportion(p, n):
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denom
    margin = z * math.sqrt((p*(1-p) + z**2/(4*n)) / n) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


# ══════════════════════════════════════════════
#  Equipment / T분기 손실 계산
# ══════════════════════════════════════════════

def calc_equipment_loss_mpa(total_flow_lpm):
    """공급배관 밸브류 손실 (전체 유량 기준)"""
    supply_id_m = constants.get_inner_diameter_m(SUPPLY_PIPE_SIZE)
    V = hydraulics.velocity_from_flow(total_flow_lpm, supply_id_m)
    total = 0.0
    for info in EQUIPMENT_K_FACTORS.values():
        total += hydraulics.head_to_mpa(
            hydraulics.minor_loss(info["K"], V)
        ) * info.get("qty", 1)
    return total

def calc_tee_split_loss_mpa(total_flow_lpm, cross_main_size="80A"):
    """양방향 T분기 손실 (전체 유량, 교차배관 구경 기준)"""
    id_m = constants.get_inner_diameter_m(cross_main_size)
    V = hydraulics.velocity_from_flow(total_flow_lpm, id_m)
    return hydraulics.head_to_mpa(hydraulics.minor_loss(K_TEE_BRANCH, V))


# ══════════════════════════════════════════════
#  단방향 / 양방향 결정론적 헬퍼
# ══════════════════════════════════════════════

def _common_gen_params():
    return dict(
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        K1_base=K1_BASE,
        K2_val=K2,
        use_head_fitting=USE_HEAD_FITTING,
        branch_inlet_config=BRANCH_INLET_CONFIG,
    )

def _common_calc_params():
    return dict(
        reducer_mode=REDUCER_MODE,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
    )

def run_uni(bead_height_mm, inlet_pressure_mpa, total_flow_lpm):
    """단방향 4가지 결정론적 실행"""
    num_br = 4
    beads_2d = [[0.0]*HEADS_PER_BRANCH for _ in range(num_br)]
    beads_2d[num_br - 1] = [bead_height_mm] * HEADS_PER_BRANCH  # 최악 B#3

    sys = pn.generate_dynamic_system(
        num_branches=num_br,
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        bead_heights_2d=beads_2d,
        **_common_gen_params(),
    )
    res = pn.calculate_dynamic_system(
        sys, K3,
        equipment_k_factors=EQUIPMENT_K_FACTORS,
        **_common_calc_params(),
    )
    return {
        "terminal_mpa": res["worst_terminal_mpa"],
        "loss_pipe_mpa": res["loss_pipe_mpa"],
        "loss_fitting_mpa": res["loss_fitting_mpa"],
        "loss_bead_mpa": res["loss_bead_mpa"],
        "cross_main_loss_mpa": res["cross_main_cumulative"],
        "tee_split_loss_mpa": 0.0,
        "equipment_loss_mpa": res["equipment_loss_mpa"],
    }

def run_bi(bead_height_mm, inlet_pressure_mpa, total_flow_lpm):
    """양방향 2+2가지 결정론적 실행"""
    num_br_side = 2
    half_flow = total_flow_lpm / 2.0

    equip_loss = calc_equipment_loss_mpa(total_flow_lpm)
    tee_loss = calc_tee_split_loss_mpa(total_flow_lpm)
    p_inlet_side = inlet_pressure_mpa - equip_loss - tee_loss

    beads_2d = [[0.0]*HEADS_PER_BRANCH for _ in range(num_br_side)]
    beads_2d[num_br_side - 1] = [bead_height_mm] * HEADS_PER_BRANCH  # 최악 B#1

    sys = pn.generate_dynamic_system(
        num_branches=num_br_side,
        inlet_pressure_mpa=p_inlet_side,
        total_flow_lpm=half_flow,
        bead_heights_2d=beads_2d,
        **_common_gen_params(),
    )
    res = pn.calculate_dynamic_system(
        sys, K3,
        equipment_k_factors=None,  # 이미 차감됨
        **_common_calc_params(),
    )
    return {
        "terminal_mpa": res["worst_terminal_mpa"],
        "loss_pipe_mpa": res["loss_pipe_mpa"],
        "loss_fitting_mpa": res["loss_fitting_mpa"] + equip_loss + tee_loss,
        "loss_bead_mpa": res["loss_bead_mpa"],
        "cross_main_loss_mpa": res["cross_main_cumulative"],
        "tee_split_loss_mpa": tee_loss,
        "equipment_loss_mpa": equip_loss,
    }


# ══════════════════════════════════════════════
#  Campaign A: 결정론적 비교
# ══════════════════════════════════════════════

def run_campaign_A():
    print("\n" + "="*60)
    print("  Campaign A: 결정론적 비교 (유량 × 비드높이)")
    print("="*60)
    t0 = time.time()

    flows = [1600, 1920, 2240, 2560, 2880, 3200]
    beads = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    total = len(flows) * len(beads) * 2
    count = 0

    rows = []
    for q in flows:
        for bh in beads:
            for topo, func in [("uni", run_uni), ("bi", run_bi)]:
                count += 1
                print(f"    [{count}/{total}] {topo} Q={q} bead={bh}mm ...", end=" ", flush=True)
                r = func(bh, P_REF, q)
                margin = r["terminal_mpa"] - PASS_THRESHOLD_MPA
                rows.append({
                    "campaign": "A",
                    "topology": topo,
                    "inlet_pressure_mpa": P_REF,
                    "total_flow_lpm": q,
                    "bead_height_mm": bh,
                    "terminal_mpa": round(r["terminal_mpa"], 6),
                    "pass_fail": "PASS" if margin >= -1e-5 else "FAIL",
                    "pressure_margin_mpa": round(margin, 6),
                    "loss_pipe_mpa": round(r["loss_pipe_mpa"], 6),
                    "loss_fitting_mpa": round(r["loss_fitting_mpa"], 6),
                    "loss_bead_mpa": round(r["loss_bead_mpa"], 6),
                    "cross_main_loss_mpa": round(r["cross_main_loss_mpa"], 6),
                    "tee_split_loss_mpa": round(r["tee_split_loss_mpa"], 6),
                    "equipment_loss_mpa": round(r["equipment_loss_mpa"], 6),
                })
                print(f"term={r['terminal_mpa']:.4f}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "A_deterministic_comparison.csv")

    # ── Fig A1: 유량 vs 말단압 ──
    fig, axes = plt.subplots(2, 4, figsize=(20, 10))
    axes = axes.flatten()
    cmap = plt.cm.Reds(np.linspace(0.2, 0.95, len(beads)))

    for i, bh in enumerate(beads):
        ax = axes[i]
        for topo, ls, lbl in [("uni", "-", "단방향"), ("bi", "--", "양방향")]:
            sub = df[(df["bead_height_mm"] == bh) & (df["topology"] == topo)]
            sub = sub.sort_values("total_flow_lpm")
            ax.plot(sub["total_flow_lpm"], sub["terminal_mpa"],
                    marker="o", linestyle=ls, markersize=5, linewidth=2,
                    label=lbl, color="#e74c3c" if topo == "uni" else "#3498db")
        ax.axhline(y=0.1, color="green", linestyle=":", alpha=0.5, label="0.1 MPa")
        ax.set_xlabel("유량 (LPM)")
        ax.set_ylabel("말단 압력 (MPa)")
        ax.set_title(f"bead={bh}mm")
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    if len(beads) < 8:
        axes[-1].set_visible(False)
    fig.suptitle("A: 유량별 말단 압력 — 단방향(적) vs 양방향(청)", fontsize=14)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "A1_flow_vs_terminal.png")

    # ── Fig A2: 비드높이 vs 말단압 (유량 패널) ──
    fig, axes = plt.subplots(2, 3, figsize=(18, 10))
    axes = axes.flatten()
    for i, q in enumerate(flows):
        ax = axes[i]
        for topo, ls, lbl, clr in [("uni", "-", "단방향", "#e74c3c"), ("bi", "--", "양방향", "#3498db")]:
            sub = df[(df["total_flow_lpm"] == q) & (df["topology"] == topo)]
            sub = sub.sort_values("bead_height_mm")
            ax.plot(sub["bead_height_mm"], sub["terminal_mpa"],
                    marker="o", linestyle=ls, markersize=5, linewidth=2,
                    label=lbl, color=clr)
        ax.axhline(y=0.1, color="green", linestyle=":", alpha=0.5)
        ax.set_xlabel("비드 높이 (mm)")
        ax.set_ylabel("말단 압력 (MPa)")
        ax.set_title(f"Q={q} LPM")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
    fig.suptitle("A: 비드높이별 말단 압력 — 단방향 vs 양방향", fontsize=14)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "A2_bead_vs_terminal.png")

    # ── Fig A3: 손실분해 바차트 ──
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    for ax_idx, bh in enumerate([1.5, 3.0]):
        ax = axes[ax_idx]
        for topo_idx, (topo, lbl) in enumerate([("uni", "단방향"), ("bi", "양방향")]):
            sub = df[(df["total_flow_lpm"] == 2560) & (df["bead_height_mm"] == bh) & (df["topology"] == topo)]
            if len(sub) == 0:
                continue
            r = sub.iloc[0]
            vals = [r["loss_pipe_mpa"], r["loss_fitting_mpa"], r["loss_bead_mpa"]]
            labels = ["배관마찰", "이음쇠+밸브", "비드"]
            colors = ["#3498db", "#2ecc71", "#e74c3c"]
            bottom = 0
            for v, c, lb in zip(vals, colors, labels):
                ax.bar(topo_idx, v, bottom=bottom, color=c, alpha=0.8, edgecolor="white",
                       label=lb if topo_idx == 0 else "")
                bottom += v
            ax.text(topo_idx, bottom + 0.002, f"{bottom:.4f}", ha="center", fontsize=9)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["단방향", "양방향"])
        ax.set_ylabel("총 손실 (MPa)")
        ax.set_title(f"bead={bh}mm, Q=2560 LPM")
        if ax_idx == 0:
            ax.legend()
        ax.grid(True, alpha=0.3, axis="y")
    fig.suptitle("A: 손실 분해 비교", fontsize=14)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "A3_loss_breakdown.png")

    print(f"  Campaign A 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  Campaign B: 임계 입구압 탐색
# ══════════════════════════════════════════════

def bisect_critical(topo_func, bead_height_mm, total_flow_lpm,
                    p_lo=0.35, p_hi=0.75, tol=0.0005, max_iter=30):
    """말단 >= 0.1 MPa가 되는 최소 입구압 탐색"""
    for i in range(max_iter):
        p_mid = (p_lo + p_hi) / 2.0
        r = topo_func(bead_height_mm, p_mid, total_flow_lpm)
        if r["terminal_mpa"] >= PASS_THRESHOLD_MPA:
            p_hi = p_mid
        else:
            p_lo = p_mid
        if (p_hi - p_lo) < tol:
            break
    return p_hi, i + 1

def run_campaign_B():
    print("\n" + "="*60)
    print("  Campaign B: 임계 입구압 탐색 (비드높이별)")
    print("="*60)
    t0 = time.time()

    beads = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    Q = 2560.0
    total = len(beads) * 2
    count = 0

    rows = []
    uni_crits = {}  # bead → critical_inlet for uni

    for bh in beads:
        for topo, func in [("uni", run_uni), ("bi", run_bi)]:
            count += 1
            print(f"    [{count}/{total}] {topo} bead={bh}mm ...", end=" ", flush=True)
            crit, iters = bisect_critical(func, bh, Q)
            r = func(bh, crit, Q)

            if topo == "uni":
                uni_crits[bh] = crit

            reduction = uni_crits.get(bh, crit) - crit
            reduction_pct = (reduction / uni_crits.get(bh, crit) * 100) if uni_crits.get(bh) else 0.0

            rows.append({
                "campaign": "B",
                "topology": topo,
                "total_flow_lpm": Q,
                "bead_height_mm": bh,
                "critical_inlet_mpa": round(crit, 5),
                "terminal_at_critical_mpa": round(r["terminal_mpa"], 6),
                "loss_pipe_mpa": round(r["loss_pipe_mpa"], 6),
                "loss_fitting_mpa": round(r["loss_fitting_mpa"], 6),
                "loss_bead_mpa": round(r["loss_bead_mpa"], 6),
                "cross_main_loss_mpa": round(r["cross_main_loss_mpa"], 6),
                "tee_split_loss_mpa": round(r["tee_split_loss_mpa"], 6),
                "equipment_loss_mpa": round(r["equipment_loss_mpa"], 6),
                "bisect_iterations": iters,
                "p_reduction_vs_uni_mpa": round(reduction, 5),
                "p_reduction_pct": round(reduction_pct, 2),
            })
            print(f"crit={crit:.5f} MPa, iter={iters}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "B_critical_pressure.csv")

    # ── Fig B1: 비드 vs 임계입구압 ──
    fig, ax = plt.subplots(figsize=(10, 6))
    for topo, lbl, clr, ls in [("uni", "단방향", "#e74c3c", "-"), ("bi", "양방향", "#3498db", "--")]:
        sub = df[df["topology"] == topo].sort_values("bead_height_mm")
        ax.plot(sub["bead_height_mm"], sub["critical_inlet_mpa"],
                marker="o", color=clr, linestyle=ls, markersize=7, linewidth=2, label=lbl)

    uni_sub = df[df["topology"] == "uni"].sort_values("bead_height_mm")
    bi_sub = df[df["topology"] == "bi"].sort_values("bead_height_mm")
    ax.fill_between(uni_sub["bead_height_mm"].values,
                    bi_sub["critical_inlet_mpa"].values,
                    uni_sub["critical_inlet_mpa"].values,
                    alpha=0.15, color="green", label="양방향 절감 구간")
    ax.axhline(y=P_REF, color="gray", linestyle=":", alpha=0.5, label=f"P_REF={P_REF}")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("임계 입구압 (MPa)")
    ax.set_title("B: 비드높이별 임계 입구압 — 단방향 vs 양방향")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "B1_critical_pressure.png")

    # ── Fig B2: 절감량 바차트 ──
    fig, ax = plt.subplots(figsize=(10, 5))
    bi_rows = df[df["topology"] == "bi"].sort_values("bead_height_mm")
    bars = ax.bar(bi_rows["bead_height_mm"].astype(str),
                  bi_rows["p_reduction_vs_uni_mpa"] * 1000,
                  color="#2ecc71", alpha=0.8, edgecolor="white")
    for bar, val in zip(bars, bi_rows["p_reduction_vs_uni_mpa"] * 1000):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f"{val:.1f}", ha="center", fontsize=9)
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("입구압 절감량 (kPa)")
    ax.set_title("B: 양방향이 절감하는 입구압 (단방향 대비)")
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "B2_pressure_savings.png")

    print(f"  Campaign B 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  Campaign C: MC 실패율 비교
# ══════════════════════════════════════════════

def run_uni_mc(bead_mm, bead_std, p_in, flow, n_iter, min_def, max_def):
    """단방향 MC — 기존 API 사용"""
    return sim.run_dynamic_monte_carlo(
        n_iterations=n_iter,
        min_defects=min_def, max_defects=max_def,
        bead_height_mm=bead_mm, bead_height_std_mm=bead_std,
        num_branches=4, heads_per_branch=HEADS_PER_BRANCH,
        inlet_pressure_mpa=p_in, total_flow_lpm=flow,
        K1_base=K1_BASE, K2_val=K2, K3_val=K3,
        use_head_fitting=USE_HEAD_FITTING,
        reducer_mode=REDUCER_MODE,
        equipment_k_factors=EQUIPMENT_K_FACTORS,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
        branch_inlet_config=BRANCH_INLET_CONFIG,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
    )

def run_bi_mc(bead_mm, bead_std, p_in, flow, n_iter, min_def, max_def):
    """양방향 MC — 직접 구현 (한 측만, 대칭)"""
    rng = np.random.default_rng()
    num_br_side = 2
    hpb = HEADS_PER_BRANCH
    half_flow = flow / 2.0
    total_fittings_side = num_br_side * hpb  # 16

    equip_loss = calc_equipment_loss_mpa(flow)
    tee_loss = calc_tee_split_loss_mpa(flow)
    p_inlet_side = p_in - equip_loss - tee_loss

    worst_pressures = np.zeros(n_iter)

    for trial in range(n_iter):
        n_def = rng.integers(min_def, min(max_def, total_fittings_side) + 1)
        positions = rng.choice(total_fittings_side, size=n_def, replace=False)

        beads_2d = [[0.0]*hpb for _ in range(num_br_side)]
        for idx in positions:
            b = idx // hpb
            h = idx % hpb
            if bead_std > 0:
                beads_2d[b][h] = max(0.0, rng.normal(bead_mm, bead_std))
            else:
                beads_2d[b][h] = bead_mm

        sys_side = pn.generate_dynamic_system(
            num_branches=num_br_side,
            inlet_pressure_mpa=p_inlet_side,
            total_flow_lpm=half_flow,
            bead_heights_2d=beads_2d,
            **_common_gen_params(),
        )
        res = pn.calculate_dynamic_system(
            sys_side, K3,
            equipment_k_factors=None,
            **_common_calc_params(),
        )
        worst_pressures[trial] = res["worst_terminal_mpa"]

    below = np.sum(worst_pressures < PASS_THRESHOLD_MPA)
    return {
        "terminal_pressures": worst_pressures,
        "p_below_threshold": float(below / n_iter),
        "mean_pressure": float(np.mean(worst_pressures)),
        "std_pressure": float(np.std(worst_pressures)),
    }

def run_campaign_C():
    print("\n" + "="*60)
    print("  Campaign C: MC 실패율 비교 (유량 × 비드높이, σ=50%)")
    print("="*60)
    t0 = time.time()

    flows = [2240, 2560, 2880]
    beads_mu = [1.0, 1.5, 2.0, 2.5]
    MC_ITER = 5000
    MIN_DEF, MAX_DEF = 1, 3

    total = len(flows) * len(beads_mu) * 2
    count = 0

    rows = []
    for q in flows:
        for mu in beads_mu:
            sig = mu * 0.5
            for topo in ["uni", "bi"]:
                count += 1
                print(f"    [{count}/{total}] {topo} Q={q} bead={mu}mm(±{sig}) ...",
                      end=" ", flush=True)

                if topo == "uni":
                    mc = run_uni_mc(mu, sig, P_REF, q, MC_ITER, MIN_DEF, MAX_DEF)
                else:
                    mc = run_bi_mc(mu, sig, P_REF, q, MC_ITER, MIN_DEF, MAX_DEF)

                pf = mc["p_below_threshold"]
                ci_lo, ci_hi = ci95_proportion(pf, MC_ITER)
                ps = mc["terminal_pressures"]

                rows.append({
                    "campaign": "C",
                    "topology": topo,
                    "inlet_pressure_mpa": P_REF,
                    "total_flow_lpm": q,
                    "bead_height_mm": mu,
                    "bead_height_std_mm": sig,
                    "mc_iterations": MC_ITER,
                    "min_defects": MIN_DEF,
                    "max_defects": MAX_DEF,
                    "fail_rate": round(pf, 6),
                    "fail_rate_CI95_low": round(ci_lo, 6),
                    "fail_rate_CI95_high": round(ci_hi, 6),
                    "mean_terminal_mpa": round(mc["mean_pressure"], 6),
                    "std_terminal_mpa": round(mc["std_pressure"], 6),
                    "P5_terminal_mpa": round(float(np.percentile(ps, 5)), 6),
                    "P50_terminal_mpa": round(float(np.percentile(ps, 50)), 6),
                    "P95_terminal_mpa": round(float(np.percentile(ps, 95)), 6),
                })
                print(f"Pf={pf*100:.2f}%")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "C_mc_failure_rate.csv")

    # ── Fig C1: 비드 vs 실패율 곡선 ──
    fig, axes = plt.subplots(1, len(flows), figsize=(6*len(flows), 5))
    if len(flows) == 1:
        axes = [axes]
    for ax_idx, q in enumerate(flows):
        ax = axes[ax_idx]
        for topo, lbl, clr, ls in [("uni", "단방향", "#e74c3c", "-"), ("bi", "양방향", "#3498db", "--")]:
            sub = df[(df["total_flow_lpm"] == q) & (df["topology"] == topo)].sort_values("bead_height_mm")
            ax.errorbar(sub["bead_height_mm"], sub["fail_rate"] * 100,
                        yerr=[(sub["fail_rate"] - sub["fail_rate_CI95_low"])*100,
                              (sub["fail_rate_CI95_high"] - sub["fail_rate"])*100],
                        marker="o", color=clr, linestyle=ls, markersize=6,
                        linewidth=2, capsize=3, label=lbl)
        ax.set_xlabel("비드 높이 (mm)")
        ax.set_ylabel("실패율 (%)")
        ax.set_title(f"Q={q} LPM")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(-5, 105)
    fig.suptitle("C: MC 실패율 — 단방향 vs 양방향 (MC=5000, defect=1~3)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "C1_failrate_curves.png")

    # ── Fig C2: 유량별 실패율 바차트 ──
    fig, axes = plt.subplots(1, len(beads_mu), figsize=(5*len(beads_mu), 5))
    if len(beads_mu) == 1:
        axes = [axes]
    for ax_idx, mu in enumerate(beads_mu):
        ax = axes[ax_idx]
        sub = df[df["bead_height_mm"] == mu].sort_values(["total_flow_lpm", "topology"])
        x = np.arange(len(flows))
        w = 0.35
        for i, (topo, lbl, clr) in enumerate([("uni", "단방향", "#e74c3c"), ("bi", "양방향", "#3498db")]):
            vals = sub[sub["topology"] == topo].sort_values("total_flow_lpm")["fail_rate"] * 100
            ax.bar(x + i*w, vals, w, label=lbl, color=clr, alpha=0.8, edgecolor="white")
        ax.set_xticks(x + w/2)
        ax.set_xticklabels([str(q) for q in flows], fontsize=9)
        ax.set_xlabel("유량 (LPM)")
        ax.set_ylabel("실패율 (%)")
        ax.set_title(f"bead={mu}mm (σ={mu*0.5:.1f})")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3, axis="y")
    fig.suptitle("C: 유량별 실패율 비교 (MC=5000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "C2_failrate_barplot.png")

    print(f"  Campaign C 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════

ALL_CASES = {"A": run_campaign_A, "B": run_campaign_B, "C": run_campaign_C}

def main():
    parser = argparse.ArgumentParser(description="단방향 vs 양방향 배관 비교")
    parser.add_argument("--case", type=str, help="캠페인 선택 (A, B, C)")
    args = parser.parse_args()

    ensure_dirs()

    print("="*60)
    print("  단방향(4가지) vs 양방향(2+2가지) 배관 비교 시뮬레이션")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  P_REF = {P_REF} MPa")
    print(f"  물성치: rho={constants.RHO}, eps={constants.EPSILON_MM}mm, nu={constants.NU:.6e}")
    print(f"  K_TEE_BRANCH = {K_TEE_BRANCH} (양방향 T분기)")
    print("="*60)

    t_total = time.time()

    if args.case:
        case = args.case.upper()
        if case in ALL_CASES:
            ALL_CASES[case]()
        else:
            print(f"  오류: '{case}'는 유효하지 않음. A/B/C 중 선택.")
            sys.exit(1)
    else:
        run_campaign_A()
        run_campaign_B()
        run_campaign_C()

    print(f"\n총 실행 시간: {elapsed(t_total)}")
    print(f"출력: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
