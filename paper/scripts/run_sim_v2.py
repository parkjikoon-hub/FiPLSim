#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim 논문용 시뮬레이션 V2 — 2nd 배치 (32헤드 고정 + 입구압/비드높이 스윕)
==========================================================================
핵심: active_heads=32 고정, Q=2560 LPM, 입구압력과 비드높이를 변수로.

실행:
  PYTHONIOENCODING=utf-8 python3 run_sim_v2.py              # 전체 실행
  PYTHONIOENCODING=utf-8 python3 run_sim_v2.py --case V1    # 개별 케이스
  PYTHONIOENCODING=utf-8 python3 run_sim_v2.py --priority   # 우선 실행 (V1,D1,S1,S2,P1,P2)
  PYTHONIOENCODING=utf-8 python3 run_sim_v2.py --graphs-only # 통합 그래프만
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

import hardy_cross as hc
importlib.reload(hc)

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
# 고정 구조 파라미터 (2nd: 32헤드 고정)
# ──────────────────────────────────────────────
NUM_BRANCHES = 4
HEADS_PER_BRANCH = 8
ACTIVE_HEADS = 32                # 고정 (4×8 = 32)
TOTAL_FLOW_LPM = 2560.0         # 32 × 80 = 2560 LPM
BRANCH_SPACING_M = 3.5
HEAD_SPACING_M = 2.1
BRANCH_INLET_CONFIG = "80A-65A"
SUPPLY_PIPE_SIZE = "100A"
USE_HEAD_FITTING = True
REDUCER_MODE = "crane"
P_REF = 0.5314                   # Case B(bead=0) → 말단 0.1 MPa
PASS_THRESHOLD_MPA = 0.1

K1_BASE = constants.K1_BASE     # 0.5
K2 = constants.K2               # 2.5
K3 = constants.K3               # 1.0

EQUIPMENT_K_FACTORS = {
    "알람밸브 (습식)":     {"K": 2.0,  "qty": 1},
    "유수검지장치":        {"K": 1.0,  "qty": 1},
    "게이트밸브 (전개)":   {"K": 0.15, "qty": 2},
    "체크밸브 (스윙형)":   {"K": 2.0,  "qty": 1},
    "90° 엘보":           {"K": 0.75, "qty": 1},
    "리듀서 (점축소)":     {"K": 0.15, "qty": 1},
}

# 출력 디렉토리 (2nd sim_results)
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "2nd sim_results"
DATA_DIR = OUTPUT_DIR / "data"
FIG_DIR = OUTPUT_DIR / "figures"


# ══════════════════════════════════════════════
#  공통 유틸리티
# ══════════════════════════════════════════════

def flow_from_heads(active_heads):
    """active_heads → 총 유량 (LPM)"""
    return active_heads * 80.0


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)


def save_csv(df, path):
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  → CSV: {path.name}")


def save_fig(fig, path, dpi=200):
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → PNG: {path.name}")


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


def gen_params():
    """generate_dynamic_system 전용 (equipment/supply 제외)"""
    return dict(
        num_branches=NUM_BRANCHES,
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        branch_inlet_config=BRANCH_INLET_CONFIG,
    )


def common_params_mc():
    """MC/sensitivity 함수용 (simulation.py 함수들)"""
    return dict(
        **common_params(),
        K1_base=K1_BASE,
        K2_val=K2,
        K3_val=K3,
    )


def make_row(case_id, bead_height_mm, **extras):
    """표준 CSV 행 생성 (2nd: active_heads=32 고정)"""
    row = {
        "case_id": case_id,
        "topology": extras.get("topology", "tree"),
        "branch_inlet_config": extras.get("branch_inlet_config", BRANCH_INLET_CONFIG),
        "num_branches": NUM_BRANCHES,
        "heads_per_branch": HEADS_PER_BRANCH,
        "active_heads": ACTIVE_HEADS,
        "total_flow_lpm": TOTAL_FLOW_LPM,
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
        "ranking_or_location_id": extras.get("ranking_or_location_id", None),
        "mean_terminal_mpa": extras.get("mean_terminal_mpa", None),
        "std_terminal_mpa": extras.get("std_terminal_mpa", None),
        "P5_terminal_mpa": extras.get("P5_terminal_mpa", None),
        "P50_terminal_mpa": extras.get("P50_terminal_mpa", None),
        "P95_terminal_mpa": extras.get("P95_terminal_mpa", None),
        "fail_rate": extras.get("fail_rate", None),
        "fail_rate_CI95_low": extras.get("fail_rate_CI95_low", None),
        "fail_rate_CI95_high": extras.get("fail_rate_CI95_high", None),
    }
    # extras에서 추가 컬럼
    for k, v in extras.items():
        if k not in row:
            row[k] = v
    return row


def calc_required_extra_pressure(terminal_mpa):
    """0.1 MPa 미달 시 추가 필요 입구압 (MPa)"""
    if terminal_mpa < PASS_THRESHOLD_MPA:
        return PASS_THRESHOLD_MPA - terminal_mpa
    return 0.0


def ci95_proportion(p, n):
    """비율의 95% CI (Wilson 근사)"""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    denominator = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denominator
    margin = z * math.sqrt((p*(1-p) + z**2/(4*n)) / n) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


# ══════════════════════════════════════════════
#  V1: 내부 검증
# ══════════════════════════════════════════════

def run_V1():
    print("\n" + "="*60)
    print("  V1: 내부 검증 (해석식 vs 시뮬레이션, 32헤드)")
    print("="*60)
    t0 = time.time()

    bead_heights = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    inlet_pressures = [0.4, 0.5, 0.5314, 0.6, 0.8, 1.0]
    Q = TOTAL_FLOW_LPM

    rows = []
    for bh in bead_heights:
        for p_in in inlet_pressures:
            # 해석식 (analytical) — ΔP만 계산
            ana = pn.calculate_system_delta_p(
                total_flow_lpm=Q,
                bead_height_mm=bh,
                K1_base=K1_BASE,
                K2_val=K2,
                K3_val=K3,
                use_head_fitting=USE_HEAD_FITTING,
                reducer_mode=REDUCER_MODE,
                **common_params(),
            )

            # 시뮬레이션
            beads_2d = [[bh] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
            sys_obj = pn.generate_dynamic_system(
                inlet_pressure_mpa=p_in,
                total_flow_lpm=Q,
                bead_heights_2d=beads_2d,
                K1_base=K1_BASE,
                K2_val=K2,
                use_head_fitting=USE_HEAD_FITTING,
                **gen_params(),
            )
            sim_result = pn.calculate_dynamic_system(
                sys_obj, K3, EQUIPMENT_K_FACTORS, SUPPLY_PIPE_SIZE,
                reducer_mode=REDUCER_MODE,
            )

            dp_ana = ana["delta_p_total_mpa"]
            dp_sim = p_in - sim_result["worst_terminal_mpa"]
            error_pct = abs(dp_ana - dp_sim) / max(dp_sim, 1e-9) * 100.0

            row = make_row("V1", bh,
                inlet_pressure_mpa=p_in,
                terminal_pressure_mpa=sim_result["worst_terminal_mpa"],
                pressure_margin_mpa=sim_result["worst_terminal_mpa"] - PASS_THRESHOLD_MPA,
                pass_fail=sim_result["worst_terminal_mpa"] >= PASS_THRESHOLD_MPA,
                loss_pipe_mpa=sim_result["loss_pipe_mpa"],
                loss_fitting_mpa=sim_result["loss_fitting_mpa"],
                loss_bead_mpa=sim_result["loss_bead_mpa"],
                worst_branch_index=sim_result["worst_branch_index"],
                delta_p_analytical=dp_ana,
                delta_p_simulation=dp_sim,
                analytical_error_pct=error_pct,
            )
            rows.append(row)

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "V1_verification.csv")

    # 그래프 1: Parity plot
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.scatter(df["delta_p_analytical"], df["delta_p_simulation"], c="steelblue", s=50, zorder=5)
    lim_max = max(df["delta_p_analytical"].max(), df["delta_p_simulation"].max()) * 1.1
    ax.plot([0, lim_max], [0, lim_max], "k--", alpha=0.5, label="완벽 일치선")
    ax.set_xlabel("해석식 ΔP (MPa)")
    ax.set_ylabel("시뮬레이션 ΔP (MPa)")
    ax.set_title("V1: 해석식 vs 시뮬레이션 ΔP 일치 검증 (32헤드, 2560 LPM)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_aspect("equal")
    save_fig(fig, FIG_DIR / "V1_parity_plot.png")

    # 그래프 2: 입구압 변화에도 ΔP 일정성
    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.cm.Reds(np.linspace(0.2, 0.9, len(bead_heights)))
    for i, bh in enumerate(bead_heights):
        sub = df[df["bead_height_mm"] == bh]
        ax.plot(sub["inlet_pressure_mpa"], sub["delta_p_simulation"],
                marker="o", color=cmap[i], alpha=0.8, markersize=6,
                label=f"bead={bh}mm")
    ax.set_xlabel("입구 압력 (MPa)")
    ax.set_ylabel("시뮬레이션 ΔP (MPa)")
    ax.set_title("V1: 입구압 변화에도 ΔP 일정성 확인 (32헤드)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "V1_delta_p_consistency.png")

    print(f"  V1 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  V2: 모델 감건성 (Robustness)
# ══════════════════════════════════════════════

def run_V2():
    print("\n" + "="*60)
    print("  V2: 모델 감건성 (Robustness, 32헤드)")
    print("="*60)
    t0 = time.time()

    Q = TOTAL_FLOW_LPM
    bh = 1.5

    def run_case(reducer_mode=REDUCER_MODE, use_hf=USE_HEAD_FITTING,
                 k_base=K1_BASE, label="baseline"):
        beads_2d = [[bh] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
        sys_obj = pn.generate_dynamic_system(
            inlet_pressure_mpa=P_REF,
            total_flow_lpm=Q,
            bead_heights_2d=beads_2d,
            K1_base=k_base,
            K2_val=K2,
            use_head_fitting=use_hf,
            **gen_params(),
        )
        r = pn.calculate_dynamic_system(
            sys_obj, K3, EQUIPMENT_K_FACTORS, SUPPLY_PIPE_SIZE,
            reducer_mode=reducer_mode,
        )
        return {
            "label": label,
            "terminal_mpa": r["worst_terminal_mpa"],
            "margin_mpa": r["worst_terminal_mpa"] - PASS_THRESHOLD_MPA,
            "loss_pipe": r["loss_pipe_mpa"],
            "loss_fitting": r["loss_fitting_mpa"],
            "loss_bead": r["loss_bead_mpa"],
        }

    results = []
    baseline = run_case(label="baseline")
    results.append(baseline)

    for rm in ["crane", "sudden", "none"]:
        results.append(run_case(reducer_mode=rm, label=f"reducer={rm}"))
    for hf in [True, False]:
        results.append(run_case(use_hf=hf, label=f"head_fitting={hf}"))
    for kb in [0.45, 0.50, 0.55]:
        results.append(run_case(k_base=kb, label=f"K_base={kb}"))

    rows = []
    for r in results:
        row = make_row("V2", bh,
            terminal_pressure_mpa=r["terminal_mpa"],
            pressure_margin_mpa=r["margin_mpa"],
            loss_pipe_mpa=r["loss_pipe"],
            loss_fitting_mpa=r["loss_fitting"],
            loss_bead_mpa=r["loss_bead"],
            model_parameter=r["label"],
        )
        rows.append(row)

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "V2_robustness.csv")

    # 토네이도 차트
    baseline_term = baseline["terminal_mpa"]
    labels = []
    deltas = []
    for r in results[1:]:
        labels.append(r["label"])
        deltas.append(r["terminal_mpa"] - baseline_term)

    fig, ax = plt.subplots(figsize=(10, 6))
    y_pos = range(len(labels))
    colors = ["green" if d >= 0 else "red" for d in deltas]
    ax.barh(y_pos, [d * 1000 for d in deltas], color=colors, alpha=0.7)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("말단압력 변화량 (kPa)")
    ax.set_title(f"V2: 모델 상수 변화에 따른 말단압력 변화 (32헤드)\n(기준: {baseline_term:.4f} MPa)")
    ax.axvline(x=0, color="black", linewidth=0.8)
    ax.grid(True, alpha=0.3, axis="x")
    save_fig(fig, FIG_DIR / "V2_tornado.png")

    print(f"  V2 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  D1: 메인 성능 비교 (입구압 × 비드높이)
# ══════════════════════════════════════════════

def run_D1():
    print("\n" + "="*60)
    print("  D1: 메인 성능 비교 (입구압력 × 비드높이, 32헤드)")
    print("="*60)
    t0 = time.time()

    inlet_pressures = [0.4, 0.45, 0.5, 0.5314, 0.55, 0.6, 0.65, 0.7, 0.8, 1.0]
    bead_heights = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    Q = TOTAL_FLOW_LPM

    rows = []
    for p_in in inlet_pressures:
        for bh in bead_heights:
            result = pn.compare_dynamic_cases(
                bead_height_existing=bh,
                bead_height_new=0.0,
                inlet_pressure_mpa=p_in,
                total_flow_lpm=Q,
                K1_base=K1_BASE,
                K2_val=K2,
                K3_val=K3,
                use_head_fitting=USE_HEAD_FITTING,
                reducer_mode=REDUCER_MODE,
                **common_params(),
            )

            term_a = result["terminal_A_mpa"]
            loss_bead = result["system_A"]["loss_bead_mpa"]
            loss_total_a = p_in - term_a
            loss_bead_ratio = loss_bead / max(loss_total_a, 1e-9) * 100.0
            req_extra = calc_required_extra_pressure(term_a)

            row = make_row("D1", bh,
                inlet_pressure_mpa=p_in,
                terminal_pressure_mpa=term_a,
                pressure_margin_mpa=term_a - PASS_THRESHOLD_MPA,
                pass_fail=result["pass_fail_A"],
                loss_pipe_mpa=result["system_A"]["loss_pipe_mpa"],
                loss_fitting_mpa=result["system_A"]["loss_fitting_mpa"],
                loss_bead_mpa=loss_bead,
                loss_bead_ratio_pct=loss_bead_ratio,
                required_extra_inlet_pressure_mpa=req_extra,
                worst_branch_index=result["worst_branch_A"],
                terminal_B_mpa=result["terminal_B_mpa"],
                improvement_pct=result["improvement_pct"],
            )
            rows.append(row)

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "D1_performance.csv")

    # 그래프 1: 입구압-말단압 (비드높이별) — 핵심 그래프
    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.cm.Reds(np.linspace(0.15, 0.95, len(bead_heights)))
    for i, bh in enumerate(bead_heights):
        sub = df[df["bead_height_mm"] == bh].sort_values("inlet_pressure_mpa")
        ax.plot(sub["inlet_pressure_mpa"], sub["terminal_pressure_mpa"],
                marker="o", color=cmap[i], markersize=5, label=f"bead={bh}mm")
    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", linewidth=1.5,
               alpha=0.7, label="0.1 MPa 규정선")
    ax.axvline(x=P_REF, color="gray", linestyle=":", alpha=0.5, label=f"P_ref={P_REF}")
    ax.set_xlabel("입구 압력 (MPa)")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title("D1: 입구압력-말단압력 곡선 (비드높이별, 32헤드/2560LPM)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1_inlet_vs_terminal.png")

    # 그래프 2: 비드높이-말단압 (입구압별)
    fig, ax = plt.subplots(figsize=(10, 6))
    cmap2 = plt.cm.Blues(np.linspace(0.3, 0.9, len(inlet_pressures)))
    for i, p_in in enumerate(inlet_pressures):
        sub = df[df["inlet_pressure_mpa"] == p_in].sort_values("bead_height_mm")
        ax.plot(sub["bead_height_mm"], sub["terminal_pressure_mpa"],
                marker="s", color=cmap2[i], markersize=5,
                label=f"P_in={p_in}")
    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title("D1: 비드높이-말단압력 곡선 (입구압별, 32헤드)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1_bead_vs_terminal.png")

    # 그래프 3: 비드높이-추가손실 (P_REF 기준)
    fig, ax = plt.subplots(figsize=(10, 6))
    sub_ref = df[df["inlet_pressure_mpa"] == P_REF].sort_values("bead_height_mm")
    ax.plot(sub_ref["bead_height_mm"], sub_ref["loss_bead_mpa"] * 1000,
            marker="^", color="red", markersize=8, linewidth=2)
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("비드 추가 손실 (kPa)")
    ax.set_title(f"D1: 비드높이-추가손실 곡선 (P_in={P_REF}, 32헤드)")
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1_bead_vs_loss.png")

    # 그래프 4: 비드높이-필요입구압 (pass를 위한 최소 P_inlet)
    fig, ax = plt.subplots(figsize=(10, 6))
    sub_ref2 = df[df["inlet_pressure_mpa"] == P_REF].sort_values("bead_height_mm")
    req_pressures = P_REF + sub_ref2["required_extra_inlet_pressure_mpa"]
    ax.plot(sub_ref2["bead_height_mm"], req_pressures,
            marker="D", color="darkred", markersize=8, linewidth=2)
    ax.axhline(y=P_REF, color="gray", linestyle=":", alpha=0.5, label=f"P_ref={P_REF}")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("필요 최소 입구압 (MPa)")
    ax.set_title("D1: 비드높이별 규정 통과를 위한 최소 입구압 (32헤드)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1_bead_vs_required_pressure.png")

    print(f"  D1 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  D2: 규정 경계 분석 (이진탐색으로 임계 입구압 탐색)
# ══════════════════════════════════════════════

def run_D2():
    print("\n" + "="*60)
    print("  D2: 규정 경계 분석 (비드높이별 임계 입구압 탐색)")
    print("="*60)
    t0 = time.time()

    Q = TOTAL_FLOW_LPM
    bead_heights = np.arange(0, 3.25, 0.25)  # 0 ~ 3.0, 0.25 간격 = 13포인트

    rows = []
    for bh in bead_heights:
        bh = round(bh, 2)

        # 이진 탐색: 말단압력 = 0.1 MPa 되는 P_inlet 찾기
        p_low, p_high = 0.3, 1.5
        for _ in range(50):  # 충분한 반복
            p_mid = (p_low + p_high) / 2.0
            result = pn.compare_dynamic_cases(
                bead_height_existing=bh,
                bead_height_new=0.0,
                inlet_pressure_mpa=p_mid,
                total_flow_lpm=Q,
                K1_base=K1_BASE, K2_val=K2, K3_val=K3,
                use_head_fitting=USE_HEAD_FITTING,
                reducer_mode=REDUCER_MODE,
                **common_params(),
            )
            term_a = result["terminal_A_mpa"]
            if term_a < PASS_THRESHOLD_MPA:
                p_low = p_mid
            else:
                p_high = p_mid
            if abs(term_a - PASS_THRESHOLD_MPA) < 1e-6:
                break

        critical_p_inlet = (p_low + p_high) / 2.0

        # P_REF에서의 결과도 기록
        result_ref = pn.compare_dynamic_cases(
            bead_height_existing=bh,
            bead_height_new=0.0,
            inlet_pressure_mpa=P_REF,
            total_flow_lpm=Q,
            K1_base=K1_BASE, K2_val=K2, K3_val=K3,
            use_head_fitting=USE_HEAD_FITTING,
            reducer_mode=REDUCER_MODE,
            **common_params(),
        )
        term_ref = result_ref["terminal_A_mpa"]

        row = make_row("D2", bh,
            inlet_pressure_mpa=P_REF,
            terminal_pressure_mpa=term_ref,
            pressure_margin_mpa=term_ref - PASS_THRESHOLD_MPA,
            pass_fail=result_ref["pass_fail_A"],
            critical_inlet_pressure_mpa=critical_p_inlet,
            extra_pressure_vs_pref=critical_p_inlet - P_REF,
        )
        rows.append(row)
        print(f"    bead={bh:.2f}mm → 임계 P_in={critical_p_inlet:.4f} MPa "
              f"(ΔP vs P_ref={critical_p_inlet-P_REF:+.4f})")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "D2_regulatory_boundary.csv")

    # 그래프 1: 비드높이 vs 임계 입구압
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df["bead_height_mm"], df["critical_inlet_pressure_mpa"],
            marker="o", color="darkred", markersize=8, linewidth=2,
            label="임계 P_inlet (PASS 경계)")
    ax.axhline(y=P_REF, color="blue", linestyle="--", alpha=0.7,
               label=f"P_ref={P_REF} (비드 없음 기준)")
    ax.fill_between(df["bead_height_mm"], P_REF, df["critical_inlet_pressure_mpa"],
                     where=df["critical_inlet_pressure_mpa"] > P_REF,
                     alpha=0.15, color="red", label="추가 필요 압력")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("임계 입구압력 (MPa)")
    ax.set_title("D2: 비드높이별 규정 통과 최소 입구압력 (32헤드/2560LPM)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D2_critical_inlet_pressure.png")

    # 그래프 2: P_REF에서의 pass/fail
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = ["green" if p else "red" for p in df["pass_fail"]]
    ax.bar(df["bead_height_mm"], df["terminal_pressure_mpa"], color=colors, alpha=0.7, width=0.2)
    ax.axhline(y=PASS_THRESHOLD_MPA, color="black", linestyle="--", linewidth=1.5,
               label="0.1 MPa 규정선")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title(f"D2: P_ref={P_REF}에서 비드높이별 합격/불합격 (32헤드)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    for _, r in df.iterrows():
        label = "PASS" if r["pass_fail"] else "FAIL"
        ax.text(r["bead_height_mm"], r["terminal_pressure_mpa"] + 0.002,
                label, ha="center", fontsize=7, fontweight="bold")
    save_fig(fig, FIG_DIR / "D2_pass_fail_at_pref.png")

    print(f"  D2 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  S1: 위치 민감도
# ══════════════════════════════════════════════

def run_S1():
    print("\n" + "="*60)
    print("  S1: 위치 민감도 (단일 비드, 32헤드)")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    Q = TOTAL_FLOW_LPM

    rows = []
    for bh in bead_heights:
        print(f"    bead={bh}mm ...", end=" ", flush=True)
        result = sim.run_dynamic_sensitivity(
            bead_height_mm=bh,
            total_flow_lpm=Q,
            inlet_pressure_mpa=P_REF,
            use_head_fitting=USE_HEAD_FITTING,
            reducer_mode=REDUCER_MODE,
            **common_params_mc(),
        )
        for loc_idx in range(len(result["deltas"])):
            row = make_row("S1", bh,
                ranking_or_location_id=loc_idx,
                terminal_pressure_mpa=result["single_bead_pressures"][loc_idx],
                worst_branch_index=result["worst_branch"],
                delta_pressure_kpa=result["deltas"][loc_idx] * 1000,
                pipe_size=result["pipe_sizes"][loc_idx],
                ranking=result["ranking"].index(loc_idx) if loc_idx in result["ranking"] else None,
            )
            rows.append(row)
        print("OK")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "S1_sensitivity.csv")

    # 그래프 1: 위치별 영향도 bar chart (비드높이별 패널)
    n_bh = len(bead_heights)
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), sharey=True)
    axes = axes.flatten()
    pipe_colors = {"25A": "#e74c3c", "32A": "#e67e22", "40A": "#f1c40f",
                   "50A": "#2ecc71", "65A": "#3498db"}
    for i, bh in enumerate(bead_heights):
        ax = axes[i]
        sub = df[df["bead_height_mm"] == bh]
        bar_colors = [pipe_colors.get(ps, "gray") for ps in sub["pipe_size"]]
        ax.bar(sub["ranking_or_location_id"], sub["delta_pressure_kpa"], color=bar_colors, alpha=0.8)
        ax.set_xlabel("헤드 위치")
        if i % 3 == 0:
            ax.set_ylabel("압력 강하 (kPa)")
        ax.set_title(f"bead={bh}mm")
        ax.grid(True, alpha=0.3, axis="y")
    fig.suptitle("S1: 위치별 영향도 (32헤드, 2560 LPM)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "S1_location_bar.png")

    # 그래프 2: 비드높이별 히트맵
    fig, ax = plt.subplots(figsize=(10, 5))
    heatmap_data = []
    for bh in bead_heights:
        sub = df[df["bead_height_mm"] == bh]
        heatmap_data.append(sub["delta_pressure_kpa"].values)
    heatmap_arr = np.array(heatmap_data)
    im = ax.imshow(heatmap_arr, cmap="YlOrRd", aspect="auto")
    ax.set_yticks(range(len(bead_heights)))
    ax.set_yticklabels([f"{bh}mm" for bh in bead_heights])
    ax.set_xlabel("헤드 위치 (인덱스)")
    ax.set_ylabel("비드 높이")
    ax.set_title("S1: 비드높이 × 위치 민감도 히트맵 (32헤드)")
    plt.colorbar(im, ax=ax, label="압력 강하 (kPa)")
    for i in range(heatmap_arr.shape[0]):
        for j in range(heatmap_arr.shape[1]):
            ax.text(j, i, f"{heatmap_arr[i,j]:.1f}", ha="center", va="center", fontsize=7)
    save_fig(fig, FIG_DIR / "S1_heatmap.png")

    print(f"  S1 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  S2: 비드 개수 영향 (±50% 편차)
# ══════════════════════════════════════════════

def run_S2():
    print("\n" + "="*60)
    print("  S2: 비드 개수 영향 (32헤드, ±50% 편차)")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    defect_counts = [0, 1, 2, 4, 8]
    mc_iter = 5000
    Q = TOTAL_FLOW_LPM

    rows = []
    for bh in bead_heights:
        bh_std = bh * 0.5  # ±50% 편차
        for dc in defect_counts:
            print(f"    bead={bh}mm(±{bh_std:.2f}), defects={dc} ...", end=" ", flush=True)
            if dc == 0:
                # 결정론적
                result = pn.compare_dynamic_cases(
                    bead_height_existing=0.0,
                    bead_height_new=0.0,
                    inlet_pressure_mpa=P_REF,
                    total_flow_lpm=Q,
                    K1_base=K1_BASE, K2_val=K2, K3_val=K3,
                    use_head_fitting=USE_HEAD_FITTING,
                    reducer_mode=REDUCER_MODE,
                    **common_params(),
                )
                row = make_row("S2", 0.0,
                    bead_height_std_mm=0.0,
                    defect_count=0,
                    mc_iterations=0,
                    terminal_pressure_mpa=result["terminal_B_mpa"],
                    mean_terminal_mpa=result["terminal_B_mpa"],
                    std_terminal_mpa=0.0,
                    fail_rate=0.0 if result["terminal_B_mpa"] >= PASS_THRESHOLD_MPA else 1.0,
                )
                rows.append(row)
                print("OK (deterministic)")
            else:
                mc_result = sim.run_dynamic_monte_carlo(
                    n_iterations=mc_iter,
                    min_defects=dc,
                    max_defects=dc,
                    bead_height_mm=bh,
                    bead_height_std_mm=bh_std,
                    total_flow_lpm=Q,
                    inlet_pressure_mpa=P_REF,
                    use_head_fitting=USE_HEAD_FITTING,
                    reducer_mode=REDUCER_MODE,
                    **common_params_mc(),
                )
                pressures = mc_result["terminal_pressures"]
                pf = mc_result["p_below_threshold"]
                ci_low, ci_high = ci95_proportion(pf, mc_iter)

                row = make_row("S2", bh,
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
                print(f"Pf={pf*100:.1f}%")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "S2_defect_count.csv")

    # 그래프 1: 비드높이별 defect count vs mean terminal
    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.cm.Reds(np.linspace(0.2, 0.9, len(bead_heights)))
    for i, bh in enumerate(bead_heights):
        sub = df[(df["bead_height_mm"] == bh) & (df["defect_count"] > 0)]
        if len(sub) == 0:
            continue
        means = sub["mean_terminal_mpa"].values
        stds = sub["std_terminal_mpa"].values
        ax.errorbar(sub["defect_count"].values, means, yerr=1.96*stds,
                    marker="o", color=cmap[i], capsize=4,
                    label=f"bead={bh}mm (±{bh*0.5:.1f})")
    # dc=0 기준선
    dc0 = df[df["defect_count"] == 0]
    if len(dc0) > 0:
        ax.axhline(y=dc0["mean_terminal_mpa"].values[0], color="blue",
                   linestyle=":", alpha=0.5, label="비드 없음 (dc=0)")
    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xlabel("비드 개수 (defect_count)")
    ax.set_ylabel("평균 말단 압력 (MPa)")
    ax.set_title("S2: 비드개수별 평균말단압 (32헤드, ±50% 편차)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "S2_defect_vs_terminal.png")

    # 그래프 2: defect count vs fail probability
    fig, ax = plt.subplots(figsize=(10, 6))
    for i, bh in enumerate(bead_heights):
        sub = df[(df["bead_height_mm"] == bh) & (df["defect_count"] > 0)]
        if len(sub) == 0:
            continue
        pf_vals = sub["fail_rate"].values * 100
        ax.plot(sub["defect_count"].values, pf_vals,
                marker="o", color=cmap[i],
                label=f"bead={bh}mm (±{bh*0.5:.1f})")
    ax.set_xlabel("비드 개수 (defect_count)")
    ax.set_ylabel("실패 확률 (%)")
    ax.set_title("S2: 비드개수별 실패확률 (32헤드, ±50% 편차)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "S2_defect_vs_fail.png")

    print(f"  S2 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  P1: 확률적 체계 편차 (32헤드 + ±50% 편차)
# ══════════════════════════════════════════════

def run_P1():
    print("\n" + "="*60)
    print("  P1: 확률적 체계 편차 (32헤드, ±50% 편차)")
    print("="*60)
    t0 = time.time()

    p_bead_list = [0.05, 0.10, 0.20, 0.30]
    mu_h_list = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    inlet_pressures = [P_REF, 0.6, 0.7]
    mc_iter = 10000
    Q = TOTAL_FLOW_LPM

    total_runs = len(p_bead_list) * len(mu_h_list) * len(inlet_pressures)
    run_count = 0

    rows = []
    for p_in in inlet_pressures:
        for pb in p_bead_list:
            for mu in mu_h_list:
                sig = mu * 0.5  # ±50% 편차
                run_count += 1
                print(f"    [{run_count}/{total_runs}] P_in={p_in} p={pb} μ={mu} σ={sig:.2f} ...",
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

                row = make_row("P1", mu,
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
    save_csv(df, DATA_DIR / "P1_probabilistic.csv")

    # 그래프 1: p_bead × mu_h heatmap (P_in = P_REF 기준)
    fig, ax = plt.subplots(figsize=(8, 6))
    sub = df[df["inlet_pressure_mpa"] == P_REF]
    matrix = np.zeros((len(mu_h_list), len(p_bead_list)))
    for i, mu in enumerate(mu_h_list):
        for j, pb in enumerate(p_bead_list):
            val = sub[(sub["bead_height_mm"] == mu) & (sub["p_bead"] == pb)]["fail_rate"]
            matrix[i, j] = val.values[0] * 100 if len(val) > 0 else 0

    im = ax.imshow(matrix, cmap="Reds", aspect="auto")
    ax.set_xticks(range(len(p_bead_list)))
    ax.set_xticklabels([f"{p}" for p in p_bead_list])
    ax.set_yticks(range(len(mu_h_list)))
    ax.set_yticklabels([f"{m}mm" for m in mu_h_list])
    ax.set_xlabel("p_bead (결함 확률)")
    ax.set_ylabel("비드 높이 μ_h (mm)")
    ax.set_title(f"P1: p_bead × μ_h 실패율 히트맵\n(32헤드, P_in={P_REF}, σ=μ×0.5)")
    plt.colorbar(im, ax=ax, label="실패율 (%)")
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, f"{matrix[i,j]:.1f}%", ha="center", va="center", fontsize=9)
    save_fig(fig, FIG_DIR / "P1_heatmap_pbead_mu.png")

    # 그래프 2: P_inlet별 Pf 변화 (p_bead=0.20, 각 mu_h별)
    fig, ax = plt.subplots(figsize=(10, 6))
    sub2 = df[df["p_bead"] == 0.20]
    cmap2 = plt.cm.Reds(np.linspace(0.2, 0.9, len(mu_h_list)))
    for i, mu in enumerate(mu_h_list):
        s = sub2[sub2["bead_height_mm"] == mu].sort_values("inlet_pressure_mpa")
        ax.plot(s["inlet_pressure_mpa"], s["fail_rate"] * 100,
                marker="o", color=cmap2[i], label=f"μ={mu}mm (σ={mu*0.5:.1f})")
    ax.axvline(x=P_REF, color="gray", linestyle=":", alpha=0.5, label=f"P_ref={P_REF}")
    ax.set_xlabel("입구 압력 (MPa)")
    ax.set_ylabel("실패율 (%)")
    ax.set_title("P1: 입구압별 실패율 변화 (p_bead=0.20, 32헤드)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "P1_inlet_vs_pf.png")

    # 그래프 3: mu_h별 fail rate bar chart (P_in=P_REF, 각 p_bead)
    fig, ax = plt.subplots(figsize=(10, 6))
    sub3 = df[df["inlet_pressure_mpa"] == P_REF]
    x = np.arange(len(mu_h_list))
    w = 0.2
    pb_colors = {0.05: "#3498db", 0.10: "#2ecc71", 0.20: "#e67e22", 0.30: "#e74c3c"}
    for i_pb, pb in enumerate(p_bead_list):
        vals = []
        for mu in mu_h_list:
            v = sub3[(sub3["bead_height_mm"] == mu) & (sub3["p_bead"] == pb)]["fail_rate"]
            vals.append(v.values[0] * 100 if len(v) > 0 else 0)
        offset = (i_pb - len(p_bead_list)/2 + 0.5) * w
        ax.bar(x + offset, vals, w, label=f"p_bead={pb}", color=pb_colors[pb], alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{m}mm" for m in mu_h_list])
    ax.set_xlabel("비드 높이 μ_h (mm)")
    ax.set_ylabel("실패율 (%)")
    ax.set_title(f"P1: μ_h별 실패율 (P_in={P_REF}, σ=μ×0.5, 32헤드)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    save_fig(fig, FIG_DIR / "P1_mu_vs_failrate.png")

    print(f"  P1 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  P2: MC 수렴성 (32헤드)
# ══════════════════════════════════════════════

def run_P2():
    print("\n" + "="*60)
    print("  P2: MC 수렴성 확인 (32헤드)")
    print("="*60)
    t0 = time.time()

    Q = TOTAL_FLOW_LPM
    mc_list = [100, 300, 1000, 3000, 10000]
    mu_h = 1.5
    sig_h = mu_h * 0.5  # 0.75

    rows = []
    for mc in mc_list:
        print(f"    mc_iterations={mc} ...", end=" ", flush=True)
        mc_result = sim.run_bernoulli_monte_carlo(
            p_bead=0.2,
            n_iterations=mc,
            bead_height_mm=mu_h,
            bead_height_std_mm=sig_h,
            total_flow_lpm=Q,
            inlet_pressure_mpa=P_REF,
            use_head_fitting=USE_HEAD_FITTING,
            reducer_mode=REDUCER_MODE,
            **common_params_mc(),
        )
        pressures = mc_result["terminal_pressures"]
        pf = mc_result["p_below_threshold"]
        ci_low, ci_high = ci95_proportion(pf, mc)

        row = make_row("P2", mu_h,
            bead_height_std_mm=sig_h,
            p_bead=0.2,
            mc_iterations=mc,
            mean_terminal_mpa=mc_result["mean_pressure"],
            std_terminal_mpa=mc_result["std_pressure"],
            P5_terminal_mpa=float(np.percentile(pressures, 5)),
            P50_terminal_mpa=float(np.percentile(pressures, 50)),
            P95_terminal_mpa=float(np.percentile(pressures, 95)),
            fail_rate=pf,
            fail_rate_CI95_low=ci_low,
            fail_rate_CI95_high=ci_high,
            ci95_width=ci_high - ci_low,
        )
        rows.append(row)
        print(f"Pf={pf*100:.2f}%, CI=[{ci_low*100:.2f}, {ci_high*100:.2f}]%")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "P2_convergence.csv")

    # 그래프 1: MC iteration vs Pf
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df["mc_iterations"], df["fail_rate"] * 100, "b-o", markersize=8, label="Pf")
    ax.fill_between(df["mc_iterations"],
                     df["fail_rate_CI95_low"] * 100,
                     df["fail_rate_CI95_high"] * 100,
                     alpha=0.2, color="blue", label="95% CI")
    ax.set_xlabel("MC 반복 횟수")
    ax.set_ylabel("실패율 Pf (%)")
    ax.set_title(f"P2: MC 수렴 (32헤드, μ={mu_h}, σ={sig_h}, p_bead=0.2)")
    ax.set_xscale("log")
    ax.legend()
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "P2_convergence_pf.png")

    # 그래프 2: MC iteration vs CI 폭
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df["mc_iterations"], df["ci95_width"] * 100, "r-s", markersize=8)
    ax.set_xlabel("MC 반복 횟수")
    ax.set_ylabel("95% CI 폭 (%p)")
    ax.set_title("P2: MC 반복수에 따른 신뢰구간 폭")
    ax.set_xscale("log")
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "P2_convergence_ci.png")

    print(f"  P2 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  G1: 배관망 효과 비교 (Tree vs Grid)
# ══════════════════════════════════════════════

def run_G1():
    print("\n" + "="*60)
    print("  G1: Tree vs Grid 비교 (32헤드)")
    print("="*60)
    t0 = time.time()

    bead_heights = [0, 1.5, 3.0]
    topologies = ["tree", "grid"]
    Q = TOTAL_FLOW_LPM

    rows = []
    for bh in bead_heights:
        for topo in topologies:
            print(f"    bead={bh}, {topo} ...", end=" ", flush=True)
            result = pn.compare_dynamic_cases_with_topology(
                topology=topo,
                bead_height_existing=bh,
                bead_height_new=0.0,
                inlet_pressure_mpa=P_REF,
                total_flow_lpm=Q,
                K1_base=K1_BASE,
                K2_val=K2,
                K3_val=K3,
                use_head_fitting=USE_HEAD_FITTING,
                reducer_mode=REDUCER_MODE,
                **common_params(),
            )
            term_a = result["terminal_A_mpa"]
            row = make_row("G1", bh,
                topology=topo,
                terminal_pressure_mpa=term_a,
                pressure_margin_mpa=term_a - PASS_THRESHOLD_MPA,
                pass_fail=result["pass_fail_A"],
                loss_pipe_mpa=result["system_A"]["loss_pipe_mpa"],
                loss_fitting_mpa=result["system_A"]["loss_fitting_mpa"],
                loss_bead_mpa=result["system_A"]["loss_bead_mpa"],
                terminal_B_mpa=result["terminal_B_mpa"],
            )
            rows.append(row)
            print(f"term={term_a:.4f}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "G1_topology.csv")

    # 그래프: Tree vs Grid grouped bar chart
    fig, ax = plt.subplots(figsize=(10, 6))
    x_labels = []
    tree_vals = []
    grid_vals = []
    for bh in bead_heights:
        x_labels.append(f"bead={bh}mm")
        t_val = df[(df["bead_height_mm"]==bh) & (df["topology"]=="tree")]
        g_val = df[(df["bead_height_mm"]==bh) & (df["topology"]=="grid")]
        tree_vals.append(t_val["terminal_pressure_mpa"].values[0] if len(t_val) > 0 else 0)
        grid_vals.append(g_val["terminal_pressure_mpa"].values[0] if len(g_val) > 0 else 0)

    x = np.arange(len(x_labels))
    w = 0.35
    ax.bar(x - w/2, tree_vals, w, label="Tree", color="steelblue", alpha=0.8)
    ax.bar(x + w/2, grid_vals, w, label="Grid", color="coral", alpha=0.8)
    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=9)
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title("G1: Tree vs Grid 토폴로지 비교 (32헤드, 2560 LPM)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    # 값 표시
    for i in range(len(x_labels)):
        ax.text(x[i]-w/2, tree_vals[i]+0.002, f"{tree_vals[i]:.4f}", ha="center", fontsize=7)
        ax.text(x[i]+w/2, grid_vals[i]+0.002, f"{grid_vals[i]:.4f}", ha="center", fontsize=7)
    save_fig(fig, FIG_DIR / "G1_tree_vs_grid.png")

    print(f"  G1 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  G2: 구조 대안 비교
# ══════════════════════════════════════════════

def run_G2():
    print("\n" + "="*60)
    print("  G2: 구조 대안 비교 (분기구조별, 32헤드)")
    print("="*60)
    t0 = time.time()

    Q = TOTAL_FLOW_LPM
    configs = ["80A-50A", "80A-65A", "65A-65A"]
    bead_heights = [0, 1.5, 3.0]

    rows = []
    for config in configs:
        for bh in bead_heights:
            print(f"    {config}, bead={bh} ...", end=" ", flush=True)
            params = common_params()
            params["branch_inlet_config"] = config
            result = pn.compare_dynamic_cases(
                bead_height_existing=bh,
                bead_height_new=0.0,
                inlet_pressure_mpa=P_REF,
                total_flow_lpm=Q,
                K1_base=K1_BASE,
                K2_val=K2,
                K3_val=K3,
                use_head_fitting=USE_HEAD_FITTING,
                reducer_mode=REDUCER_MODE,
                **params,
            )
            term_a = result["terminal_A_mpa"]
            req_extra = calc_required_extra_pressure(term_a)
            row = make_row("G2", bh,
                branch_inlet_config=config,
                terminal_pressure_mpa=term_a,
                pressure_margin_mpa=term_a - PASS_THRESHOLD_MPA,
                pass_fail=result["pass_fail_A"],
                loss_pipe_mpa=result["system_A"]["loss_pipe_mpa"],
                loss_fitting_mpa=result["system_A"]["loss_fitting_mpa"],
                loss_bead_mpa=result["system_A"]["loss_bead_mpa"],
                required_extra_inlet_pressure_mpa=req_extra,
            )
            rows.append(row)
            print(f"term={term_a:.4f}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "G2_branch_config.csv")

    # 그래프: 분기구조별 성능 비교
    fig, ax = plt.subplots(figsize=(12, 6))
    x_labels = []
    term_vals = []
    bar_colors = []
    config_colors = {"80A-50A": "#3498db", "80A-65A": "#e67e22", "65A-65A": "#2ecc71"}
    for config in configs:
        for bh in bead_heights:
            x_labels.append(f"{config}\nbead={bh}")
            sub = df[(df["branch_inlet_config"]==config) & (df["bead_height_mm"]==bh)]
            term_vals.append(sub["terminal_pressure_mpa"].values[0] if len(sub) > 0 else 0)
            bar_colors.append(config_colors.get(config, "gray"))

    x = np.arange(len(x_labels))
    ax.bar(x, term_vals, color=bar_colors, alpha=0.8)
    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xticks(x)
    ax.set_xticklabels(x_labels, fontsize=8)
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title("G2: 분기구조별 성능 비교 (32헤드, 2560 LPM)")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    for i, v in enumerate(term_vals):
        ax.text(i, v + 0.002, f"{v:.4f}", ha="center", fontsize=7)
    save_fig(fig, FIG_DIR / "G2_branch_config.png")

    print(f"  G2 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  통합 그래프
# ══════════════════════════════════════════════

def run_integrated_graphs():
    """기존 CSV를 읽어서 통합 그래프 생성"""
    print("\n" + "="*60)
    print("  통합 그래프 생성")
    print("="*60)

    # D1 데이터 기반 stacked bar chart (P_REF 기준 손실 분해)
    d1_path = DATA_DIR / "D1_performance.csv"
    if d1_path.exists():
        df = pd.read_csv(d1_path)
        sub = df[df["inlet_pressure_mpa"] == P_REF].sort_values("bead_height_mm")
        if len(sub) > 0:
            fig, ax = plt.subplots(figsize=(10, 6))
            x = np.arange(len(sub))
            w = 0.6
            ax.bar(x, sub["loss_pipe_mpa"]*1000, w, label="배관 마찰", color="#3498db")
            ax.bar(x, sub["loss_fitting_mpa"]*1000, w,
                   bottom=sub["loss_pipe_mpa"]*1000, label="이음쇠", color="#e67e22")
            ax.bar(x, sub["loss_bead_mpa"]*1000, w,
                   bottom=(sub["loss_pipe_mpa"]+sub["loss_fitting_mpa"])*1000,
                   label="비드 추가", color="#e74c3c")
            ax.set_xticks(x)
            ax.set_xticklabels([f"{bh}mm" for bh in sub["bead_height_mm"]])
            ax.set_xlabel("비드 높이 (mm)")
            ax.set_ylabel("손실 (kPa)")
            ax.set_title(f"통합: 손실 분해 (32헤드, P_in={P_REF})")
            ax.legend()
            ax.grid(True, alpha=0.3, axis="y")
            save_fig(fig, FIG_DIR / "INT_stacked_loss.png")

    print("  통합 그래프 완료")


# ══════════════════════════════════════════════
#  메인 진입점
# ══════════════════════════════════════════════

ALL_CASES = {
    "V1": run_V1,
    "V2": run_V2,
    "D1": run_D1,
    "D2": run_D2,
    "S1": run_S1,
    "S2": run_S2,
    "P1": run_P1,
    "P2": run_P2,
    "G1": run_G1,
    "G2": run_G2,
}

PRIORITY_CASES = ["V1", "D1", "S1", "S2", "P1", "P2"]


def main():
    parser = argparse.ArgumentParser(description="FiPLSim 논문용 시뮬레이션 V2 (2nd: 32헤드)")
    parser.add_argument("--case", type=str, help="실행할 케이스 (V1,V2,D1,...)")
    parser.add_argument("--priority", action="store_true", help="우선 케이스만 실행 (V1,D1,S1,S2,P1,P2)")
    parser.add_argument("--graphs-only", action="store_true", help="통합 그래프만 재생성")
    parser.add_argument("--all", action="store_true", help="전체 실행")
    args = parser.parse_args()

    ensure_dirs()

    print("=" * 60)
    print("  FiPLSim 논문용 시뮬레이션 V2 (2nd: 32헤드 고정)")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  헤드: {ACTIVE_HEADS}개, 유량: {TOTAL_FLOW_LPM} LPM")
    print(f"  P_REF = {P_REF} MPa")
    print(f"  물성치: ρ={constants.RHO}, ε={constants.EPSILON_MM}mm, ν={constants.NU:.6e}")
    print("=" * 60)

    t_total = time.time()

    if args.graphs_only:
        run_integrated_graphs()
    elif args.case:
        case = args.case.upper()
        if case in ALL_CASES:
            ALL_CASES[case]()
            run_integrated_graphs()
        else:
            print(f"  오류: '{case}'는 유효한 케이스가 아닙니다.")
            print(f"  사용 가능: {', '.join(ALL_CASES.keys())}")
            sys.exit(1)
    elif args.priority:
        for case in PRIORITY_CASES:
            ALL_CASES[case]()
        run_integrated_graphs()
    elif args.all:
        for case in ALL_CASES:
            ALL_CASES[case]()
        run_integrated_graphs()
    else:
        # 기본: 전체 실행
        for case in ALL_CASES:
            ALL_CASES[case]()
        run_integrated_graphs()

    print(f"\n총 실행 시간: {elapsed(t_total)}")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print("완료!")


if __name__ == "__main__":
    main()
