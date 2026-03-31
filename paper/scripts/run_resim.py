#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim 재시뮬레이션 — 물성치 통일 + 새 P_REF 계산
====================================================
배경: run_paper_simulations.py (P_REF 산출)와 run_sim_v4.py (시뮬레이션)가
      서로 다른 물성치를 사용하여 1.32 kPa 격차 발생.
      물성치를 논문용 값으로 통일하고 새 P_REF를 재계산하여 4개 캠페인 재실행.

대상 캠페인:
  D1b — 31/32 헤드 전이 (결정론적)
  D2  — 규제 경계 분석 (bead sweep → critical inlet pressure)
  S1  — 위치 민감도 (단일 결함 × 위치)
  F   — OpenFOAM 대응용 국부 손실 추출

불필요 캠페인 (재시뮬 안 함):
  S2b, P1b — 반올림 입구압 사용, 1.3 kPa 차이 무의미
  asymmetry — K값은 입구압 무관

실행:
  PYTHONIOENCODING=utf-8 python3 run_resim.py
  PYTHONIOENCODING=utf-8 python3 run_resim.py --case D2
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
# 논문용 물성치 오버라이드 (통일 값)
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
# 고정 구조 파라미터
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

LOCATION_PIPE_MAP = {
    0: "50A", 1: "50A", 2: "50A",
    3: "40A", 4: "40A",
    5: "32A",
    6: "25A", 7: "25A",
}

# 출력 디렉토리
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "resim_results"
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
    return dict(
        num_branches=NUM_BRANCHES,
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        equipment_k_factors=EQUIPMENT_K_FACTORS,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
        branch_inlet_config=BRANCH_INLET_CONFIG,
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
    for k, v in extras.items():
        if k not in row:
            row[k] = v
    return row


def calc_required_extra_pressure(terminal_mpa):
    if terminal_mpa < PASS_THRESHOLD_MPA:
        return PASS_THRESHOLD_MPA - terminal_mpa
    return 0.0


def run_deterministic(bead_height_mm, inlet_pressure_mpa=None,
                      active_heads=ACTIVE_HEADS, total_flow_lpm=None):
    """결정론적 시뮬레이션 실행"""
    if inlet_pressure_mpa is None:
        inlet_pressure_mpa = P_REF
    if total_flow_lpm is None:
        total_flow_lpm = active_heads * 80.0
    result = pn.compare_dynamic_cases(
        bead_height_existing=bead_height_mm,
        bead_height_new=0.0,
        inlet_pressure_mpa=inlet_pressure_mpa,
        total_flow_lpm=total_flow_lpm,
        K1_base=K1_BASE, K2_val=K2, K3_val=K3,
        use_head_fitting=USE_HEAD_FITTING,
        reducer_mode=REDUCER_MODE,
        **common_params(),
    )
    return result


def find_critical_inlet_pressure(bead_height_mm, target_terminal=0.1,
                                 p_low=0.40, p_high=1.0, tol=1e-6):
    """이진 탐색으로 말단 = target_terminal MPa 달성에 필요한 입구압 계산"""
    for _ in range(200):
        p_mid = (p_low + p_high) / 2.0
        result = run_deterministic(bead_height_mm, inlet_pressure_mpa=p_mid)
        term = result["terminal_A_mpa"]
        if abs(term - target_terminal) < tol:
            return p_mid
        if term < target_terminal:
            p_low = p_mid
        else:
            p_high = p_mid
    return (p_low + p_high) / 2.0


# ══════════════════════════════════════════════
#  P_REF 자동 계산
# ══════════════════════════════════════════════

def calculate_new_pref():
    """통일 물성치로 새 P_REF 계산 (bead=0, 32헤드, 말단=0.1 MPa)"""
    print("\n" + "="*60)
    print("  Step 0: 새 P_REF 계산 (bead=0, 32헤드, 말단=0.1 MPa)")
    print("="*60)
    t0 = time.time()

    new_pref = find_critical_inlet_pressure(
        bead_height_mm=0.0, target_terminal=0.1,
        p_low=0.40, p_high=1.0, tol=1e-7
    )

    # 검증: 새 P_REF에서 bead=0 말단 압력 확인
    verify = run_deterministic(0.0, inlet_pressure_mpa=new_pref)
    term_verify = verify["terminal_A_mpa"]

    print(f"  새 P_REF = {new_pref:.6f} MPa")
    print(f"  검증: bead=0, 32헤드 → 말단 = {term_verify:.6f} MPa")
    print(f"  오차: |{term_verify} - 0.1| = {abs(term_verify - 0.1)*1e6:.1f} Pa")
    print(f"  물성치: rho={constants.RHO}, eps={constants.EPSILON_MM}mm, "
          f"nu={constants.NU:.6e}")
    print(f"  ({elapsed(t0)})")

    return new_pref


# ── P_REF 글로벌 변수 (main에서 설정) ──
P_REF = None


# ══════════════════════════════════════════════
#  캠페인 D2: 결정론적 규제 경계
# ══════════════════════════════════════════════

def run_D2():
    print("\n" + "="*60)
    print(f"  D2: 결정론적 규제 경계 (P_REF={P_REF:.6f})")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]

    rows = []
    for i, bh in enumerate(bead_heights):
        print(f"    [{i+1}/{len(bead_heights)}] bead={bh}mm ...", end=" ", flush=True)

        result = run_deterministic(bh, inlet_pressure_mpa=P_REF)
        term = result["terminal_A_mpa"]
        sys_a = result["system_A"]

        crit_p = find_critical_inlet_pressure(bh)
        extra_vs_pref = crit_p - P_REF

        # pass_fail 판정: margin >= -1e-6 (수치 오차 허용)
        margin = term - PASS_THRESHOLD_MPA
        if abs(margin) < 1e-5:
            pf_str = "BOUNDARY"
        elif margin >= 0:
            pf_str = "PASS"
        else:
            pf_str = "FAIL"

        row = make_row("D2", bh,
            inlet_pressure_mpa=P_REF,
            terminal_pressure_mpa=term,
            pressure_margin_mpa=margin,
            pass_fail=pf_str,
            loss_pipe_mpa=sys_a["loss_pipe_mpa"],
            loss_fitting_mpa=sys_a["loss_fitting_mpa"],
            loss_bead_mpa=sys_a["loss_bead_mpa"],
            worst_branch_index=result["worst_branch_A"],
            critical_inlet_pressure_mpa=round(crit_p, 6),
            extra_pressure_vs_pref=round(extra_vs_pref, 6),
        )
        rows.append(row)
        print(f"term={term:.5f}, crit_P_in={crit_p:.5f}, {pf_str}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "D2_regulatory_boundary.csv")

    # ── 그래프 1: bead_height vs critical_inlet_pressure ──
    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax1.plot(df["bead_height_mm"], df["critical_inlet_pressure_mpa"],
             "o-", color="#e74c3c", markersize=7, linewidth=2, label="임계 입구압 (P_crit)")
    ax1.axhline(y=P_REF, color="blue", linestyle="--", linewidth=1.5, alpha=0.7,
                label=f"P_REF = {P_REF:.5f} MPa")
    ax1.set_xlabel("비드 높이 (mm)")
    ax1.set_ylabel("임계 입구 압력 (MPa)")
    ax1.set_title("D2: 비드 높이별 임계 입구압 (말단 = 0.1 MPa 기준)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()
    ax2.bar(df["bead_height_mm"], df["extra_pressure_vs_pref"] * 1000,
            width=0.18, alpha=0.3, color="orange", label="추가 압력 (kPa)")
    ax2.set_ylabel("P_REF 대비 추가 압력 (kPa)")
    ax2.legend(loc="upper right")

    plt.tight_layout()
    save_fig(fig, FIG_DIR / "D2_bead_vs_critical_inlet.png")

    # ── 그래프 2: bead_height vs terminal_pressure @P_REF ──
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(df["bead_height_mm"], df["terminal_pressure_mpa"],
            "s-", color="#2ecc71", markersize=7, linewidth=2, label=f"말단 압력 @P_REF={P_REF:.5f}")
    ax.axhline(y=PASS_THRESHOLD_MPA, color="red", linestyle="--", linewidth=1.5,
               alpha=0.7, label="0.1 MPa 규정선")
    ax.fill_between(df["bead_height_mm"], PASS_THRESHOLD_MPA,
                    df["terminal_pressure_mpa"],
                    where=df["terminal_pressure_mpa"] >= PASS_THRESHOLD_MPA,
                    alpha=0.15, color="green", label="PASS 영역")
    ax.fill_between(df["bead_height_mm"], 0,
                    df["terminal_pressure_mpa"],
                    where=df["terminal_pressure_mpa"] < PASS_THRESHOLD_MPA,
                    alpha=0.15, color="red", label="FAIL 영역")
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title(f"D2: 비드 높이별 말단 압력 (P_in = {P_REF:.5f} MPa)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "D2_bead_vs_terminal_at_pref.png")

    # ── 그래프 3: 3항 분리 손실 ──
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.stackplot(df["bead_height_mm"],
                 df["loss_pipe_mpa"], df["loss_fitting_mpa"], df["loss_bead_mpa"],
                 labels=["배관 마찰", "이음쇠 기본", "비드 추가"],
                 colors=["#3498db", "#2ecc71", "#e74c3c"], alpha=0.7)
    ax.set_xlabel("비드 높이 (mm)")
    ax.set_ylabel("손실 (MPa)")
    ax.set_title("D2: 비드 높이별 3항 분리 손실")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "D2_loss_breakdown.png")

    print(f"  D2 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  캠페인 D1b: 31/32 헤드 전이
# ══════════════════════════════════════════════

def run_D1b():
    print("\n" + "="*60)
    print(f"  D1b: 31/32 헤드 비교 (P_REF={P_REF:.6f})")
    print("="*60)
    t0 = time.time()

    heads_list = [31, 32]
    bead_heights = [0.0, 1.5, 2.5, 3.0]

    rows = []
    for ah in heads_list:
        flow = ah * 80.0
        for bh in bead_heights:
            print(f"    heads={ah} bead={bh}mm Q={flow} LPM ...", end=" ", flush=True)

            result = run_deterministic(bh, inlet_pressure_mpa=P_REF,
                                       active_heads=ah, total_flow_lpm=flow)
            term_a = result["terminal_A_mpa"]
            sys_a = result["system_A"]

            margin = term_a - PASS_THRESHOLD_MPA
            if abs(margin) < 1e-5:
                pf_str = "BOUNDARY"
            elif margin >= 0:
                pf_str = "PASS"
            else:
                pf_str = "FAIL"

            row = make_row("D1b", bh,
                active_heads=ah,
                total_flow_lpm=flow,
                inlet_pressure_mpa=P_REF,
                terminal_pressure_mpa=term_a,
                pressure_margin_mpa=margin,
                pass_fail=pf_str,
                loss_pipe_mpa=sys_a["loss_pipe_mpa"],
                loss_fitting_mpa=sys_a["loss_fitting_mpa"],
                loss_bead_mpa=sys_a["loss_bead_mpa"],
                required_extra_inlet_pressure_mpa=calc_required_extra_pressure(term_a),
                worst_branch_index=result["worst_branch_A"],
                terminal_B_mpa=result["terminal_B_mpa"],
                improvement_pct=result["improvement_pct"],
            )
            rows.append(row)
            print(f"term={term_a:.5f} MPa, {pf_str}")

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
        for _, r in sub.iterrows():
            ax.annotate(f"{r['terminal_pressure_mpa']:.5f}",
                       (r["active_heads"], r["terminal_pressure_mpa"]),
                       textcoords="offset points", xytext=(10, 5), fontsize=8)

    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", linewidth=1.5,
               alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xlabel("활성 헤드 수")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title(f"D1b: 31/32 헤드 전이 (P_in={P_REF:.5f})")
    ax.set_xticks(heads_list)
    ax.legend()
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1b_heads_vs_terminal.png")

    print(f"  D1b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  캠페인 S1: 위치 민감도
# ══════════════════════════════════════════════

def run_S1():
    print("\n" + "="*60)
    print(f"  S1: 위치 민감도 (P_REF={P_REF:.6f})")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    locations = list(range(HEADS_PER_BRANCH))  # 0~7

    rows = []
    total_runs = len(bead_heights) * len(locations)
    run_count = 0

    # baseline 계산 (bead 없음)
    base_system = pn.generate_dynamic_system(
        num_branches=NUM_BRANCHES,
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        inlet_pressure_mpa=P_REF,
        total_flow_lpm=TOTAL_FLOW_LPM,
        K1_base=K1_BASE,
        K2_val=K2,
        use_head_fitting=USE_HEAD_FITTING,
        branch_inlet_config=BRANCH_INLET_CONFIG,
    )
    base_result = pn.calculate_dynamic_system(
        base_system, K3,
        reducer_mode=REDUCER_MODE,
        equipment_k_factors=EQUIPMENT_K_FACTORS,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
    )
    baseline_term = base_result["worst_terminal_mpa"]
    print(f"  baseline (bead=0) 말단 = {baseline_term:.6f} MPa")

    for bh in bead_heights:
        for loc in locations:
            run_count += 1
            pipe_size = LOCATION_PIPE_MAP[loc]
            print(f"    [{run_count}/{total_runs}] bead={bh}mm loc={loc}({pipe_size}) ...",
                  end=" ", flush=True)

            beads_2d = [[0.0] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
            worst_branch = NUM_BRANCHES - 1
            beads_2d[worst_branch][loc] = bh

            system = pn.generate_dynamic_system(
                bead_heights_2d=beads_2d,
                num_branches=NUM_BRANCHES,
                heads_per_branch=HEADS_PER_BRANCH,
                branch_spacing_m=BRANCH_SPACING_M,
                head_spacing_m=HEAD_SPACING_M,
                inlet_pressure_mpa=P_REF,
                total_flow_lpm=TOTAL_FLOW_LPM,
                K1_base=K1_BASE,
                K2_val=K2,
                use_head_fitting=USE_HEAD_FITTING,
                branch_inlet_config=BRANCH_INLET_CONFIG,
            )
            result = pn.calculate_dynamic_system(
                system, K3,
                reducer_mode=REDUCER_MODE,
                equipment_k_factors=EQUIPMENT_K_FACTORS,
                supply_pipe_size=SUPPLY_PIPE_SIZE,
            )

            term = result["worst_terminal_mpa"]
            delta_kpa = (baseline_term - term) * 1000.0

            row = make_row("S1", bh,
                inlet_pressure_mpa=P_REF,
                ranking_or_location_id=loc,
                pipe_size=pipe_size,
                terminal_pressure_mpa=term,
                pressure_margin_mpa=term - PASS_THRESHOLD_MPA,
                pass_fail="PASS" if term >= PASS_THRESHOLD_MPA else "FAIL",
                delta_pressure_kpa=round(delta_kpa, 4),
                baseline_terminal_mpa=baseline_term,
            )
            rows.append(row)
            print(f"delta={delta_kpa:.2f} kPa")

    df = pd.DataFrame(rows)
    df["ranking"] = df.groupby("bead_height_mm")["delta_pressure_kpa"].rank(
        ascending=False, method="min").astype(int)
    save_csv(df, DATA_DIR / "S1_sensitivity.csv")

    # ── 그래프 1: 위치별 delta_pressure ──
    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.cm.Reds(np.linspace(0.2, 0.95, len(bead_heights)))
    for i, bh in enumerate(bead_heights):
        sub = df[df["bead_height_mm"] == bh].sort_values("ranking_or_location_id")
        ax.plot(sub["ranking_or_location_id"], sub["delta_pressure_kpa"],
                "o-", color=cmap[i], markersize=7, linewidth=2,
                label=f"bead={bh}mm")
    ax.set_xlabel("위치 ID (0=50A 입구측 ~ 7=25A 말단측)")
    ax.set_ylabel("압력 강하량 (kPa)")
    ax.set_title("S1: 단일 비드 위치별 압력 민감도")
    ax.set_xticks(locations)
    ax.set_xticklabels([f"{loc}\n({LOCATION_PIPE_MAP[loc]})" for loc in locations], fontsize=8)
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "S1_location_vs_delta.png")

    # ── 그래프 2: bead=1.5mm 바 차트 ──
    fig, ax = plt.subplots(figsize=(10, 5))
    sub15 = df[df["bead_height_mm"] == 1.5].sort_values("ranking_or_location_id")
    colors = ["#3498db" if ps in ["50A", "65A"] else
              "#e67e22" if ps in ["40A"] else
              "#e74c3c" for ps in sub15["pipe_size"]]
    bars = ax.bar(sub15["ranking_or_location_id"], sub15["delta_pressure_kpa"],
                  color=colors, alpha=0.8, edgecolor="white")
    for bar, val in zip(bars, sub15["delta_pressure_kpa"]):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.05,
                f"{val:.2f}", ha="center", fontsize=8)
    ax.set_xlabel("위치 ID (관경)")
    ax.set_ylabel("압력 강하량 (kPa)")
    ax.set_title("S1: 단일 비드 위치별 영향도 (bead=1.5mm)")
    ax.set_xticks(locations)
    ax.set_xticklabels([f"{loc}\n({LOCATION_PIPE_MAP[loc]})" for loc in locations], fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "S1_sensitivity_bar_1p5mm.png")

    print(f"  S1 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  캠페인 F: OpenFOAM 대응용 국부 손실 추출
# ══════════════════════════════════════════════

def run_F():
    print("\n" + "="*60)
    print(f"  F: OpenFOAM 대응용 국부 손실 추출 (P_REF={P_REF:.6f})")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.0, 1.5, 3.0]
    target_locations = [6, 3]

    rows = []
    case_counter = 0

    for loc in target_locations:
        pipe_size = LOCATION_PIPE_MAP[loc]
        for bh in bead_heights:
            case_counter += 1
            comp_id = f"F{case_counter:02d}_{pipe_size}_{bh:.1f}mm"
            print(f"    {comp_id} ...", end=" ", flush=True)

            beads_2d = [[0.0] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
            worst_branch = NUM_BRANCHES - 1
            beads_2d[worst_branch][loc] = bh

            system = pn.generate_dynamic_system(
                bead_heights_2d=beads_2d,
                num_branches=NUM_BRANCHES,
                heads_per_branch=HEADS_PER_BRANCH,
                branch_spacing_m=BRANCH_SPACING_M,
                head_spacing_m=HEAD_SPACING_M,
                inlet_pressure_mpa=P_REF,
                total_flow_lpm=TOTAL_FLOW_LPM,
                K1_base=K1_BASE,
                K2_val=K2,
                use_head_fitting=USE_HEAD_FITTING,
                branch_inlet_config=BRANCH_INLET_CONFIG,
            )
            result = pn.calculate_dynamic_system(
                system, K3,
                reducer_mode=REDUCER_MODE,
                equipment_k_factors=EQUIPMENT_K_FACTORS,
                supply_pipe_size=SUPPLY_PIPE_SIZE,
            )

            profile = result["branch_profiles"][worst_branch]
            seg = profile["segment_details"][loc]

            # baseline (bead=0)
            beads_base = [[0.0] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
            sys_base = pn.generate_dynamic_system(
                bead_heights_2d=beads_base,
                num_branches=NUM_BRANCHES,
                heads_per_branch=HEADS_PER_BRANCH,
                branch_spacing_m=BRANCH_SPACING_M,
                head_spacing_m=HEAD_SPACING_M,
                inlet_pressure_mpa=P_REF,
                total_flow_lpm=TOTAL_FLOW_LPM,
                K1_base=K1_BASE,
                K2_val=K2,
                use_head_fitting=USE_HEAD_FITTING,
                branch_inlet_config=BRANCH_INLET_CONFIG,
            )
            res_base = pn.calculate_dynamic_system(
                sys_base, K3,
                reducer_mode=REDUCER_MODE,
                equipment_k_factors=EQUIPMENT_K_FACTORS,
                supply_pipe_size=SUPPLY_PIPE_SIZE,
            )
            base_seg = res_base["branch_profiles"][worst_branch]["segment_details"][loc]

            local_dp_mpa = seg["K1_loss_mpa"]
            local_dp_kpa = local_dp_mpa * 1000.0
            base_dp_mpa = base_seg["K1_loss_mpa"]

            K_eff = seg["K1_value"]
            K_base_val = base_seg["K1_value"]

            V = seg["velocity_ms"]
            Re = seg["reynolds"]
            D_mm = seg["inner_diameter_mm"]
            D_eff_mm = D_mm - 2.0 * bh if bh > 0 else D_mm

            dp_ratio = local_dp_mpa / base_dp_mpa if base_dp_mpa > 0 else 1.0
            K_ratio = K_eff / K_base_val if K_base_val > 0 else 1.0

            branch_flow = TOTAL_FLOW_LPM / NUM_BRANCHES
            head_flow = branch_flow / HEADS_PER_BRANCH
            segment_flow = branch_flow - loc * head_flow
            run_side_flow = segment_flow - head_flow

            row = {
                "comparison_case_id": comp_id,
                "topology": "tree",
                "branch_inlet_config": BRANCH_INLET_CONFIG,
                "active_heads": ACTIVE_HEADS,
                "inlet_pressure_mpa": P_REF,
                "ranking_or_location_id": loc,
                "pipe_size": pipe_size,
                "bead_height_mm": bh,
                "local_pressure_drop_mpa": round(local_dp_mpa, 6),
                "local_pressure_drop_kpa": round(local_dp_kpa, 4),
                "equivalent_local_K": round(K_eff, 4),
                "branch_side_flow_lpm": round(segment_flow, 2),
                "run_side_flow_lpm": round(run_side_flow, 2),
                "baseline_relative_dp_ratio": round(dp_ratio, 4),
                "baseline_relative_K_ratio": round(K_ratio, 4),
                "effective_diameter_mm": round(D_eff_mm, 2),
                "K_base": round(K_base_val, 4),
                "K_eff": round(K_eff, 4),
                "local_velocity_m_s": round(V, 4),
                "Re_local": round(Re, 0),
                "note": f"loc={loc}, {pipe_size}, bead={bh}mm",
            }
            rows.append(row)
            print(f"dp={local_dp_kpa:.2f} kPa, K={K_eff:.4f}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "FiPLSim_local_loss_for_openfoam_comparison.csv")

    # ── 그래프 ──
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for ax_idx, loc in enumerate(target_locations):
        ax = axes[ax_idx]
        pipe_size = LOCATION_PIPE_MAP[loc]
        sub = df[df["ranking_or_location_id"] == loc]

        ax.bar(sub["bead_height_mm"].astype(str), sub["local_pressure_drop_kpa"],
               color=["#3498db", "#e67e22", "#e74c3c"], alpha=0.8, edgecolor="white")
        for i, (_, r) in enumerate(sub.iterrows()):
            ax.text(i, r["local_pressure_drop_kpa"] + 0.02,
                    f"{r['local_pressure_drop_kpa']:.3f}\nK={r['equivalent_local_K']:.4f}",
                    ha="center", fontsize=9)
        ax.set_xlabel("비드 높이 (mm)")
        ax.set_ylabel("국부 압력 강하 (kPa)")
        ax.set_title(f"위치 {loc} ({pipe_size}) — OpenFOAM 대응")
        ax.grid(True, alpha=0.3, axis="y")

    fig.suptitle("캠페인 F: 국부 손실 추출 — OpenFOAM 비교용", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "F_local_loss_openfoam.png")

    print(f"  F 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  메인 진입점
# ══════════════════════════════════════════════

ALL_CASES = {
    "D2":  run_D2,
    "D1B": run_D1b,
    "S1":  run_S1,
    "F":   run_F,
}


def main():
    global P_REF

    parser = argparse.ArgumentParser(description="FiPLSim 재시뮬레이션 (물성치 통일)")
    parser.add_argument("--case", type=str, help="실행할 케이스 (D2, D1b, S1, F)")
    args = parser.parse_args()

    ensure_dirs()

    # Step 0: 새 P_REF 계산
    P_REF = calculate_new_pref()

    print("\n" + "=" * 60)
    print("  FiPLSim 재시뮬레이션 (물성치 통일)")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  새 P_REF = {P_REF:.6f} MPa")
    print(f"  헤드: {ACTIVE_HEADS}개, 유량: {TOTAL_FLOW_LPM} LPM")
    print(f"  물성치: rho={constants.RHO}, eps={constants.EPSILON_MM}mm, nu={constants.NU:.6e}")
    print(f"  구조: {BRANCH_INLET_CONFIG}, 헤드간격={HEAD_SPACING_M}m")
    print(f"  대상: D1b, D2, S1, F (4개 캠페인)")
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
        run_D1b()
        run_D2()
        run_S1()
        run_F()

    # 결과 요약 저장
    summary = {
        "resimulation_date": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "new_P_REF_mpa": round(P_REF, 6),
        "old_P_REF_mpa": 0.5314,
        "delta_pref_kpa": round((P_REF - 0.5314) * 1000, 3),
        "physics": {
            "epsilon_mm": constants.EPSILON_MM,
            "rho_kg_m3": constants.RHO,
            "nu_m2_s": constants.NU,
        },
        "campaigns_rerun": ["D1b", "D2", "S1", "F"],
        "campaigns_unchanged": ["S2b", "P1b", "asymmetry"],
    }
    import json
    with open(DATA_DIR / "resim_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n  -> JSON: resim_summary.json")

    print(f"\n총 실행 시간: {elapsed(t_total)}")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print("재시뮬레이션 완료!")


if __name__ == "__main__":
    main()
