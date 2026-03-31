#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim 논문용 시뮬레이션 V4 — 4th 배치 (논문 보강 + OpenFOAM 대응)
==========================================================================
명세: FiPLSim_시뮬레이션_입출력_명세.md 기반 6개 캠페인

캠페인:
  A (D2)  — 결정론적 규제 경계 (bead height sweep → critical inlet pressure)
  B (D1b) — 31/32 헤드 전이 (결정론적)
  C (S1)  — 위치 민감도 (단일 결함 × 위치 × bead height)
  D (S2b) — 결함 개수 전이 (defect_count × inlet pressure, MC)
  E (P1b) — 확률론적 전이 (Bernoulli MC, p_bead × bead height × inlet pressure)
  F       — OpenFOAM 대응용 국부 손실 추출

실행:
  PYTHONIOENCODING=utf-8 python3 run_sim_v4.py              # 전체 실행
  PYTHONIOENCODING=utf-8 python3 run_sim_v4.py --case D2    # 개별 케이스
  PYTHONIOENCODING=utf-8 python3 run_sim_v4.py --case S1
  PYTHONIOENCODING=utf-8 python3 run_sim_v4.py --case F
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
# 고정 구조 파라미터 (명세서 §2.1 공통 고정 조건)
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

# 위치 → 관경 매핑 (명세서 §3.3)
LOCATION_PIPE_MAP = {
    0: "50A", 1: "50A", 2: "50A",
    3: "40A", 4: "40A",
    5: "32A",
    6: "25A", 7: "25A",
}

# 출력 디렉토리
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "4th sim_results"
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


def common_params_mc():
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
    if terminal_mpa < PASS_THRESHOLD_MPA:
        return PASS_THRESHOLD_MPA - terminal_mpa
    return 0.0


def run_deterministic(bead_height_mm, inlet_pressure_mpa=P_REF,
                      active_heads=ACTIVE_HEADS, total_flow_lpm=None):
    """결정론적 시뮬레이션 (모든 접합부 동일 비드 높이) 실행, 전체 결과 반환"""
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
                                 p_low=0.40, p_high=1.0, tol=1e-5):
    """이진 탐색으로 말단 0.1 MPa 달성에 필요한 critical inlet pressure 계산"""
    for _ in range(100):
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
#  캠페인 A (D2): 결정론적 규제 경계
# ══════════════════════════════════════════════

def run_D2():
    print("\n" + "="*60)
    print("  캠페인 A (D2): 결정론적 규제 경계 — bead sweep → critical P_in")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0]

    rows = []
    for i, bh in enumerate(bead_heights):
        print(f"    [{i+1}/{len(bead_heights)}] bead={bh}mm ...", end=" ", flush=True)

        # P_REF 기준 말단 압력
        result = run_deterministic(bh, inlet_pressure_mpa=P_REF)
        term = result["terminal_A_mpa"]
        sys_a = result["system_A"]

        # critical inlet pressure (말단 = 0.1 MPa 달성)
        crit_p = find_critical_inlet_pressure(bh)
        extra_vs_pref = crit_p - P_REF

        row = make_row("D2", bh,
            inlet_pressure_mpa=P_REF,
            terminal_pressure_mpa=term,
            pressure_margin_mpa=term - PASS_THRESHOLD_MPA,
            pass_fail=term >= PASS_THRESHOLD_MPA,
            loss_pipe_mpa=sys_a["loss_pipe_mpa"],
            loss_fitting_mpa=sys_a["loss_fitting_mpa"],
            loss_bead_mpa=sys_a["loss_bead_mpa"],
            worst_branch_index=result["worst_branch_A"],
            critical_inlet_pressure_mpa=round(crit_p, 6),
            extra_pressure_vs_pref=round(extra_vs_pref, 6),
        )
        rows.append(row)
        print(f"term={term:.4f}, crit_P_in={crit_p:.4f}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "D2_regulatory_boundary.csv")

    # ── 그래프 1: bead_height vs critical_inlet_pressure ──
    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax1.plot(df["bead_height_mm"], df["critical_inlet_pressure_mpa"],
             "o-", color="#e74c3c", markersize=7, linewidth=2, label="임계 입구압 (P_crit)")
    ax1.axhline(y=P_REF, color="blue", linestyle="--", linewidth=1.5, alpha=0.7,
                label=f"P_REF = {P_REF} MPa")
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
            "s-", color="#2ecc71", markersize=7, linewidth=2, label="말단 압력 @P_REF")
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
    ax.set_title(f"D2: 비드 높이별 말단 압력 (P_in = {P_REF} MPa)")
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
#  캠페인 B (D1b): 31/32 헤드 전이
# ══════════════════════════════════════════════

def run_D1b():
    print("\n" + "="*60)
    print("  캠페인 B (D1b): 31/32 헤드 비교 (결정론적)")
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

            row = make_row("D1b", bh,
                active_heads=ah,
                total_flow_lpm=flow,
                inlet_pressure_mpa=P_REF,
                terminal_pressure_mpa=term_a,
                pressure_margin_mpa=term_a - PASS_THRESHOLD_MPA,
                pass_fail=term_a >= PASS_THRESHOLD_MPA,
                loss_pipe_mpa=sys_a["loss_pipe_mpa"],
                loss_fitting_mpa=sys_a["loss_fitting_mpa"],
                loss_bead_mpa=sys_a["loss_bead_mpa"],
                required_extra_inlet_pressure_mpa=calc_required_extra_pressure(term_a),
                worst_branch_index=result["worst_branch_A"],
                terminal_B_mpa=result["terminal_B_mpa"],
                improvement_pct=result["improvement_pct"],
            )
            rows.append(row)
            pf_str = "PASS" if term_a >= PASS_THRESHOLD_MPA else "FAIL"
            print(f"term={term_a:.4f} MPa, {pf_str}")

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
            ax.annotate(f"{r['terminal_pressure_mpa']:.4f}",
                       (r["active_heads"], r["terminal_pressure_mpa"]),
                       textcoords="offset points", xytext=(10, 5), fontsize=8)

    ax.axhline(y=PASS_THRESHOLD_MPA, color="green", linestyle="--", linewidth=1.5,
               alpha=0.7, label="0.1 MPa 규정선")
    ax.set_xlabel("활성 헤드 수")
    ax.set_ylabel("말단 압력 (MPa)")
    ax.set_title(f"D1b: 31/32 헤드 전이 (P_in={P_REF})")
    ax.set_xticks(heads_list)
    ax.legend()
    ax.grid(True, alpha=0.3)
    save_fig(fig, FIG_DIR / "D1b_heads_vs_terminal.png")

    print(f"  D1b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  캠페인 C (S1): 위치 민감도
# ══════════════════════════════════════════════

def run_S1():
    print("\n" + "="*60)
    print("  캠페인 C (S1): 위치 민감도 (단일 결함 × 위치)")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    locations = list(range(HEADS_PER_BRANCH))  # 0~7

    rows = []
    total_runs = len(bead_heights) * len(locations)
    run_count = 0

    for bh in bead_heights:
        for loc in locations:
            run_count += 1
            pipe_size = LOCATION_PIPE_MAP[loc]
            print(f"    [{run_count}/{total_runs}] bead={bh}mm loc={loc}({pipe_size}) ...",
                  end=" ", flush=True)

            # 최악 가지배관(B#3, 0-indexed)의 특정 위치에만 비드
            beads_2d = [[0.0] * HEADS_PER_BRANCH for _ in range(NUM_BRANCHES)]
            worst_branch = NUM_BRANCHES - 1  # B#3 (가장 먼 가지배관)
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

            # baseline (비드 없음)도 필요 — 첫 루프 시 계산 후 캐시
            if not hasattr(run_S1, '_baseline'):
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
                run_S1._baseline = base_result["worst_terminal_mpa"]

            baseline_term = run_S1._baseline
            delta_kpa = (baseline_term - term) * 1000.0

            row = make_row("S1", bh,
                inlet_pressure_mpa=P_REF,
                ranking_or_location_id=loc,
                pipe_size=pipe_size,
                terminal_pressure_mpa=term,
                pressure_margin_mpa=term - PASS_THRESHOLD_MPA,
                pass_fail=term >= PASS_THRESHOLD_MPA,
                delta_pressure_kpa=round(delta_kpa, 4),
                baseline_terminal_mpa=baseline_term,
            )
            rows.append(row)
            print(f"delta={delta_kpa:.2f} kPa")

    df = pd.DataFrame(rows)

    # 각 bead_height 그룹 내에서 delta 기준 랭킹 추가
    df["ranking"] = df.groupby("bead_height_mm")["delta_pressure_kpa"].rank(
        ascending=False, method="min").astype(int)
    save_csv(df, DATA_DIR / "S1_sensitivity.csv")

    # baseline 캐시 제거
    if hasattr(run_S1, '_baseline'):
        del run_S1._baseline

    # ── 그래프 1: 위치별 delta_pressure (bead 높이별 곡선) ──
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

    # ── 그래프 2: bead=1.5mm 기준 히트맵 스타일 바 차트 ──
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
#  캠페인 D (S2b): 결함 개수 × 입구압 전이구간
# ══════════════════════════════════════════════

def run_S2b():
    print("\n" + "="*60)
    print("  캠페인 D (S2b): 결함 개수 × 입구압 전이구간 (MC)")
    print("="*60)
    t0 = time.time()

    inlet_pressures = [0.55, 0.56, 0.58, 0.60]
    defect_counts = [0, 1, 2, 4, 8]
    bead_combos = [  # (bead_height_mm, bead_height_std_mm)
        (0.0, 0.0),
        (1.5, 0.75),
        (2.0, 1.0),
        (2.5, 1.25),
        (3.0, 1.5),
    ]
    mc_iter = 10000

    total_runs = len(inlet_pressures) * len(defect_counts) * len(bead_combos)
    run_count = 0

    rows = []
    for p_in in inlet_pressures:
        for bh, bh_std in bead_combos:
            for dc in defect_counts:
                run_count += 1
                print(f"    [{run_count}/{total_runs}] P_in={p_in} bead={bh}(+/-{bh_std}) dc={dc} ...",
                      end=" ", flush=True)

                if dc == 0:
                    # baseline 결정론적
                    result = run_deterministic(0.0, inlet_pressure_mpa=p_in)
                    term = result["terminal_B_mpa"]
                    row = make_row("S2b", bh,
                        inlet_pressure_mpa=p_in,
                        bead_height_std_mm=bh_std,
                        defect_count=0,
                        mc_iterations=0,
                        terminal_pressure_mpa=term,
                        mean_terminal_mpa=term,
                        std_terminal_mpa=0.0,
                        fail_rate=0.0 if term >= PASS_THRESHOLD_MPA else 1.0,
                    )
                    rows.append(row)
                    print(f"OK (baseline, term={term:.4f})")
                else:
                    if bh == 0.0:
                        # bead=0이면 dc>0이어도 비드 없음과 동일 → 스킵
                        continue
                    mc_result = sim.run_dynamic_monte_carlo(
                        n_iterations=mc_iter,
                        min_defects=dc,
                        max_defects=dc,
                        bead_height_mm=bh,
                        bead_height_std_mm=bh_std,
                        total_flow_lpm=TOTAL_FLOW_LPM,
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

    # ── 그래프: 입구압별 defect_count vs fail_rate ──
    bead_heights_plot = [1.5, 2.0, 2.5, 3.0]
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    axes = axes.flatten()
    cmap = plt.cm.Reds(np.linspace(0.3, 0.95, len(bead_heights_plot)))

    for ax_idx, p_in in enumerate(inlet_pressures):
        ax = axes[ax_idx]
        sub_p = df[df["inlet_pressure_mpa"] == p_in]
        for i, bh in enumerate(bead_heights_plot):
            sub = sub_p[(sub_p["bead_height_mm"] == bh) & (sub_p["defect_count"] > 0)]
            if len(sub) == 0:
                continue
            ax.plot(sub["defect_count"].values, sub["fail_rate"].values * 100,
                    marker="o", color=cmap[i], markersize=6,
                    label=f"bead={bh}mm (+/-{bh*0.5:.1f})")
        dc0 = sub_p[(sub_p["defect_count"] == 0) & (sub_p["bead_height_mm"] == 0.0)]
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

    fig.suptitle("S2b: 비드 개수별 실패율 — 전이구간 (32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "S2b_defect_vs_fail_transition.png")

    print(f"  S2b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  캠페인 E (P1b): 확률론적 전이
# ══════════════════════════════════════════════

def run_P1b():
    print("\n" + "="*60)
    print("  캠페인 E (P1b): 확률론적 전이 (Bernoulli MC)")
    print("="*60)
    t0 = time.time()

    inlet_pressures = [0.54, 0.55, 0.56, 0.58, 0.60]
    p_bead_list = [0.05, 0.10, 0.20, 0.30]
    bead_combos = [  # (bead_height_mm, bead_height_std_mm)
        (1.0, 0.5),
        (1.5, 0.75),
        (2.0, 1.0),
        (2.5, 1.25),
        (3.0, 1.5),
    ]
    mc_iter = 10000

    total_runs = len(inlet_pressures) * len(p_bead_list) * len(bead_combos)
    run_count = 0

    rows = []
    for p_in in inlet_pressures:
        for pb in p_bead_list:
            for mu, sig in bead_combos:
                run_count += 1
                print(f"    [{run_count}/{total_runs}] P_in={p_in} p={pb} mu={mu} sig={sig} ...",
                      end=" ", flush=True)

                mc_result = sim.run_bernoulli_monte_carlo(
                    p_bead=pb,
                    n_iterations=mc_iter,
                    bead_height_mm=mu,
                    bead_height_std_mm=sig,
                    total_flow_lpm=TOTAL_FLOW_LPM,
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
    mu_h_list = [c[0] for c in bead_combos]
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
        ax.set_ylabel("bead_height (mm)")
        ax.set_title(f"P_in={p_in}", fontsize=10)
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                ax.text(j, i, f"{matrix[i,j]:.1f}%", ha="center", va="center", fontsize=7)

    fig.suptitle("P1b: p_bead × bead_height 실패율 히트맵 (32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "P1b_heatmap_transition.png")

    # ── 그래프 2: 입구압 vs Pf 곡선 (worst case 조건) ──
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

    fig.suptitle("P1b: 입구압별 실패율 곡선 (32헤드, MC=10000)", fontsize=13)
    plt.tight_layout()
    save_fig(fig, FIG_DIR / "P1b_inlet_vs_pf_transition.png")

    print(f"  P1b 완료 ({elapsed(t0)})")
    return df


# ══════════════════════════════════════════════
#  캠페인 F: OpenFOAM 대응용 국부 손실 추출
# ══════════════════════════════════════════════

def run_F():
    print("\n" + "="*60)
    print("  캠페인 F: OpenFOAM 대응용 국부 손실 추출")
    print("="*60)
    t0 = time.time()

    bead_heights = [0.0, 1.5, 3.0]
    target_locations = [6, 3]  # 우선순위 1: 25A, 우선순위 2: 40A

    rows = []
    case_counter = 0

    for loc in target_locations:
        pipe_size = LOCATION_PIPE_MAP[loc]
        for bh in bead_heights:
            case_counter += 1
            comp_id = f"F{case_counter:02d}_{pipe_size}_{bh:.1f}mm"
            print(f"    {comp_id} ...", end=" ", flush=True)

            # 최악 가지배관의 해당 위치에만 비드
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

            # 최악 가지배관의 segment_details에서 해당 위치 추출
            profile = result["branch_profiles"][worst_branch]
            seg = profile["segment_details"][loc]

            # baseline (bead=0) 데이터 추출
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

            # 국부 손실 계산
            local_dp_mpa = seg["K1_loss_mpa"]  # K1 (비드 포함) 손실
            local_dp_kpa = local_dp_mpa * 1000.0
            base_dp_mpa = base_seg["K1_loss_mpa"]
            base_dp_kpa = base_dp_mpa * 1000.0

            # K_eff
            K_eff = seg["K1_value"]
            K_base = base_seg["K1_value"]

            # 유속, Re
            V = seg["velocity_ms"]
            Re = seg["reynolds"]
            D_mm = seg["inner_diameter_mm"]

            # effective diameter
            D_eff_mm = D_mm - 2.0 * bh if bh > 0 else D_mm

            # 상대 비교
            dp_ratio = local_dp_mpa / base_dp_mpa if base_dp_mpa > 0 else 1.0
            K_ratio = K_eff / K_base if K_base > 0 else 1.0

            # 유량 분리: 해당 위치에서의 branch/run side flow
            branch_flow = TOTAL_FLOW_LPM / NUM_BRANCHES  # 640 LPM
            head_flow = branch_flow / HEADS_PER_BRANCH     # 80 LPM
            segment_flow = branch_flow - loc * head_flow   # 해당 위치 통과 유량
            run_side_flow = segment_flow - head_flow        # 직진 유량

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
                "K_base": round(K_base, 4),
                "K_eff": round(K_eff, 4),
                "local_velocity_m_s": round(V, 4),
                "Re_local": round(Re, 0),
                "note": f"loc={loc}, {pipe_size}, bead={bh}mm",
            }
            rows.append(row)
            print(f"dp={local_dp_kpa:.2f} kPa, K={K_eff:.4f}")

    df = pd.DataFrame(rows)
    save_csv(df, DATA_DIR / "FiPLSim_local_loss_for_openfoam_comparison.csv")

    # ── 그래프: 위치별 bead vs local_pressure_drop ──
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
    "S2B": run_S2b,
    "P1B": run_P1b,
    "F":   run_F,
}


def main():
    parser = argparse.ArgumentParser(description="FiPLSim 논문용 4th 시뮬레이션 (논문 보강 + OpenFOAM)")
    parser.add_argument("--case", type=str, help="실행할 케이스 (D2, D1b, S1, S2b, P1b, F)")
    args = parser.parse_args()

    ensure_dirs()

    print("=" * 60)
    print("  FiPLSim 논문용 시뮬레이션 V4 (4th: 논문 보강 + OpenFOAM)")
    print(f"  시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  헤드: {ACTIVE_HEADS}개, 유량: {TOTAL_FLOW_LPM} LPM")
    print(f"  P_REF = {P_REF} MPa")
    print(f"  물성치: rho={constants.RHO}, eps={constants.EPSILON_MM}mm, nu={constants.NU:.6e}")
    print(f"  구조: {BRANCH_INLET_CONFIG}, 헤드간격={HEAD_SPACING_M}m")
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
        # 전체 실행: 빠른 것 → 느린 것 순서
        run_D1b()    # B: 빠름 (8건)
        run_D2()     # A: 빠름 (13건, 이진탐색 포함)
        run_S1()     # C: 빠름 (48건 결정론적)
        run_F()      # F: 빠름 (6건 결정론적)
        run_S2b()    # D: 느림 (MC 10000)
        run_P1b()    # E: 가장 느림 (MC 10000)

    print(f"\n총 실행 시간: {elapsed(t_total)}")
    print(f"출력 디렉토리: {OUTPUT_DIR}")
    print("완료!")


if __name__ == "__main__":
    main()
