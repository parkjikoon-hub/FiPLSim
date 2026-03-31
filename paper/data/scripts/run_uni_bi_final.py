#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim: 단방향 vs 양방향 비교 — 토폴로지 교정본
================================================
단방향(uni): 이경엘보 90° K=0.53 (NFPA 13), 65A 입구관 없음
양방향(bi): T분기 K=1.0 + K_TEE_BRANCH_80A=1.06 (NFPA 13)
결함 배치: 최악 가지배관(B#3/B#1)에 집중 (보수적 시나리오)
MC 반복: 10,000회 고정

캠페인:
  A-1  설계유량 결정론 비교         (70건)
  A-2  유량 sweep 결정론 비교       (420건)
  B-1  설계유량 임계압력 비교       (70건)
  B-2  유량-비드 임계압력 맵        (378건)
  C-1  입구압 전이구간 신뢰성       (1,344 MC)
  C-2  기준선 (결함 없음)           (28건)
  D-1  유량 전이구간 신뢰성         (960 MC)
  E-1  위치 민감도                  (48건)

실행:
  PYTHONIOENCODING=utf-8 python3 run_uni_bi_final.py
  PYTHONIOENCODING=utf-8 python3 run_uni_bi_final.py --case A1
  PYTHONIOENCODING=utf-8 python3 run_uni_bi_final.py --smoke
"""

import sys
import os
import argparse
import time
import math
import json
import importlib
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# ── 물성치 오버라이드 (논문 통합물성치) ──────────────────
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

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ══════════════════════════════════════════════════════════
#  고정 상수
# ══════════════════════════════════════════════════════════
HEADS_PER_BRANCH = 8
NUM_BRANCHES = 4
HEAD_FLOW_LPM = 80.0
ACTIVE_HEADS = NUM_BRANCHES * HEADS_PER_BRANCH
DESIGN_FLOW_LPM = ACTIVE_HEADS * HEAD_FLOW_LPM
BRANCH_SPACING_M = 3.5
HEAD_SPACING_M = 2.1
BRANCH_INLET_CONFIG = "80A-65A"
SUPPLY_PIPE_SIZE = "100A"
USE_HEAD_FITTING = True
REDUCER_MODE = "crane"
PASS_THRESHOLD_MPA = 0.1
P_REF = None  # main()에서 bisect 자동 역산

K1_BASE = constants.K1_BASE
K2 = constants.K2
K3 = constants.K3
K_ELBOW_90_65A = constants.K_ELBOW_90_65A  # 이경엘보 90° 65A (NFPA 13)
K_TEE_BRANCH = constants.K_TEE_BRANCH_80A  # 1.06 (NFPA 13)

MC_ITER = 10_000

EQUIPMENT_K_FACTORS = {
    "알람밸브 (습식)":   {"K": 2.0,  "qty": 1},
    "유수검지장치":      {"K": 1.0,  "qty": 1},
    "게이트밸브 (전개)": {"K": 0.15, "qty": 2},
    "체크밸브 (스윙형)": {"K": 2.0,  "qty": 1},
    "90도 엘보":        {"K": 0.75, "qty": 1},
    "리듀서 (점축소)":   {"K": 0.15, "qty": 1},
}

# ── 실험 변수 범위 ─────────────────────────────────────
TOPOS = ["uni", "bi"]
DEFECT_COUNTS = [0, 1, 2, 3, 4]
BEAD_DET = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]       # 결정론 비드높이
BEAD_MC = [1.0, 1.5, 2.0, 2.5]                          # 확률론 비드 평균
BEAD_STD = [0.25, 0.50, 0.75]                            # 확률론 비드 표준편차
FLOW_TREND = None   # main()에서 DESIGN_FLOW_LPM 기반 동적 생성
FLOW_TRANS = None   # main()에서 DESIGN_FLOW_LPM 기반 동적 생성
PIN_TRANS  = None   # main()에서 P_REF 기반 동적 생성

OUT_DIR = Path("uni_bi_simulation_results")
DATA_DIR = OUT_DIR / "data"
FIG_DIR = OUT_DIR / "figures"


# ══════════════════════════════════════════════════════════
#  유틸리티 함수
# ══════════════════════════════════════════════════════════

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

def save_csv(df, name):
    path = DATA_DIR / name
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"    CSV 저장: {path}")

def save_fig(fig, name, dpi=200):
    path = FIG_DIR / name
    fig.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"    그림 저장: {path}")

def elapsed(t0):
    dt = time.time() - t0
    if dt < 60:
        return f"{dt:.1f}초"
    return f"{dt/60:.1f}분"


# ══════════════════════════════════════════════════════════
#  핵심 계산 함수
# ══════════════════════════════════════════════════════════

def wilson_ci95(p_hat, n):
    """이항분포 Wilson score 95% 신뢰구간"""
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    denom = 1 + z**2 / n
    centre = (p_hat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def build_beads_2d(num_branches, positions, bead_mm, bead_std=0.0, rng=None):
    """
    지정 위치에 비드 배치. positions = [(branch, head), ...]
    bead_std > 0 이면 정규분포 샘플링, 음수는 0으로 절단.
    """
    beads = [[0.0] * HEADS_PER_BRANCH for _ in range(num_branches)]
    for b, h in positions:
        if bead_std > 0 and rng is not None:
            beads[b][h] = max(0.0, rng.normal(bead_mm, bead_std))
        else:
            beads[b][h] = bead_mm
    return beads


def worst_branch_positions(num_branches, defect_count):
    """최악 가지배관(마지막)에 결함 N개 배치 — 위치 0(입구측)부터 순서대로"""
    worst = num_branches - 1
    return [(worst, h) for h in range(min(defect_count, HEADS_PER_BRANCH))]


def calc_equipment_loss_mpa(total_flow_lpm):
    """공급배관 장비 손실 (전체 유량, 100A 기준)"""
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


def _gen_params(topo):
    """토폴로지별 generate_dynamic_system 파라미터 (uni: 이경엘보, bi: T분기)"""
    return dict(
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        K1_base=K1_BASE,
        K2_val=K2,
        use_head_fitting=USE_HEAD_FITTING,
        branch_inlet_config="80A-65A-elbow" if topo == "uni" else BRANCH_INLET_CONFIG,
    )


def _common_calc_params():
    return dict(
        reducer_mode=REDUCER_MODE,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
    )


# ══════════════════════════════════════════════════════════
#  결정론적 실행
# ══════════════════════════════════════════════════════════

def run_det(topo, defect_count, bead_mm, p_in, flow):
    """결정론적 단일 실행 — 최악측 집중 배치"""
    if topo == "uni":
        num_br = 4
        positions = worst_branch_positions(num_br, defect_count)
        beads_2d = build_beads_2d(num_br, positions, bead_mm)
        sys = pn.generate_dynamic_system(
            num_branches=num_br,
            inlet_pressure_mpa=p_in,
            total_flow_lpm=flow,
            bead_heights_2d=beads_2d,
            **_gen_params("uni"),
        )
        res = pn.calculate_dynamic_system(
            sys, K_ELBOW_90_65A,
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
    else:  # bi
        num_br = 2
        equip_loss = calc_equipment_loss_mpa(flow)
        tee_loss = calc_tee_split_loss_mpa(flow)
        p_side = p_in - equip_loss - tee_loss
        half_flow = flow / 2.0
        positions = worst_branch_positions(num_br, defect_count)
        beads_2d = build_beads_2d(num_br, positions, bead_mm)
        sys = pn.generate_dynamic_system(
            num_branches=num_br,
            inlet_pressure_mpa=p_side,
            total_flow_lpm=half_flow,
            bead_heights_2d=beads_2d,
            **_gen_params("bi"),
        )
        res = pn.calculate_dynamic_system(
            sys, K3,
            equipment_k_factors=None,
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


# ══════════════════════════════════════════════════════════
#  위치 지정 결정론적 실행 (E-1용)
# ══════════════════════════════════════════════════════════

def run_det_at_position(topo, head_pos, bead_mm, p_in, flow):
    """결함 1개를 지정 위치에 배치하여 실행"""
    if topo == "uni":
        num_br = 4
        positions = [(num_br - 1, head_pos)]
        beads_2d = build_beads_2d(num_br, positions, bead_mm)
        sys = pn.generate_dynamic_system(
            num_branches=num_br, inlet_pressure_mpa=p_in,
            total_flow_lpm=flow, bead_heights_2d=beads_2d,
            **_gen_params("uni"),
        )
        res = pn.calculate_dynamic_system(
            sys, K_ELBOW_90_65A, equipment_k_factors=EQUIPMENT_K_FACTORS,
            **_common_calc_params(),
        )
        return res["worst_terminal_mpa"]
    else:
        num_br = 2
        equip_loss = calc_equipment_loss_mpa(flow)
        tee_loss = calc_tee_split_loss_mpa(flow)
        p_side = p_in - equip_loss - tee_loss
        positions = [(num_br - 1, head_pos)]
        beads_2d = build_beads_2d(num_br, positions, bead_mm)
        sys = pn.generate_dynamic_system(
            num_branches=num_br, inlet_pressure_mpa=p_side,
            total_flow_lpm=flow / 2.0, bead_heights_2d=beads_2d,
            **_gen_params("bi"),
        )
        res = pn.calculate_dynamic_system(
            sys, K3, equipment_k_factors=None,
            **_common_calc_params(),
        )
        return res["worst_terminal_mpa"]


# ══════════════════════════════════════════════════════════
#  Monte Carlo 실행
# ══════════════════════════════════════════════════════════

def run_mc(topo, defect_count, bead_mm, bead_std, p_in, flow,
           mc_iter=MC_ITER, seed=42):
    """최악측 집중 배치 MC — bead 높이만 정규분포 샘플링"""
    rng = np.random.default_rng(seed)
    terminals = np.empty(mc_iter)

    if topo == "uni":
        num_br = 4
        positions = worst_branch_positions(num_br, defect_count)
        for i in range(mc_iter):
            beads_2d = build_beads_2d(num_br, positions, bead_mm, bead_std, rng)
            sys = pn.generate_dynamic_system(
                num_branches=num_br, inlet_pressure_mpa=p_in,
                total_flow_lpm=flow, bead_heights_2d=beads_2d,
                **_gen_params("uni"),
            )
            res = pn.calculate_dynamic_system(
                sys, K_ELBOW_90_65A, equipment_k_factors=EQUIPMENT_K_FACTORS,
                **_common_calc_params(),
            )
            terminals[i] = res["worst_terminal_mpa"]
    else:  # bi
        num_br = 2
        equip_loss = calc_equipment_loss_mpa(flow)
        tee_loss = calc_tee_split_loss_mpa(flow)
        p_side = p_in - equip_loss - tee_loss
        half_flow = flow / 2.0
        positions = worst_branch_positions(num_br, defect_count)
        for i in range(mc_iter):
            beads_2d = build_beads_2d(num_br, positions, bead_mm, bead_std, rng)
            sys = pn.generate_dynamic_system(
                num_branches=num_br, inlet_pressure_mpa=p_side,
                total_flow_lpm=half_flow, bead_heights_2d=beads_2d,
                **_gen_params("bi"),
            )
            res = pn.calculate_dynamic_system(
                sys, K3, equipment_k_factors=None,
                **_common_calc_params(),
            )
            terminals[i] = res["worst_terminal_mpa"]

    # 통계 계산
    fail_count = int(np.sum(terminals < PASS_THRESHOLD_MPA))
    fail_rate = fail_count / mc_iter
    ci_low, ci_high = wilson_ci95(fail_rate, mc_iter)
    p5, p50, p95 = np.percentile(terminals, [5, 50, 95])

    return {
        "fail_rate": fail_rate,
        "fail_rate_CI95_low": ci_low,
        "fail_rate_CI95_high": ci_high,
        "mean_terminal_mpa": float(np.mean(terminals)),
        "std_terminal_mpa": float(np.std(terminals)),
        "P5_terminal_mpa": float(p5),
        "P50_terminal_mpa": float(p50),
        "P95_terminal_mpa": float(p95),
    }


# ══════════════════════════════════════════════════════════
#  이분탐색 (임계 입구압)
# ══════════════════════════════════════════════════════════

def bisect_critical(topo, defect_count, bead_mm, flow,
                    p_lo=0.35, p_hi=0.75, tol=0.0005, max_iter=40):
    """말단 >= 0.1 MPa 를 만족하는 최소 입구압 탐색"""
    for _ in range(max_iter):
        p_mid = (p_lo + p_hi) / 2.0
        r = run_det(topo, defect_count, bead_mm, p_mid, flow)
        if r["terminal_mpa"] >= PASS_THRESHOLD_MPA:
            p_hi = p_mid
        else:
            p_lo = p_mid
        if (p_hi - p_lo) < tol:
            break
    return p_hi


# ══════════════════════════════════════════════════════════
#  Campaign A-1: 설계유량 결정론 비교
# ══════════════════════════════════════════════════════════

def run_campaign_A1():
    print("\n" + "=" * 60)
    print("  Campaign A-1: 설계유량 결정론 비교 (70건)")
    print("=" * 60)
    t0 = time.time()
    rows = []
    total = len(TOPOS) * len(BEAD_DET) * len(DEFECT_COUNTS)
    done = 0

    for topo in TOPOS:
        for bead in BEAD_DET:
            for dc in DEFECT_COUNTS:
                r = run_det(topo, dc, bead, P_REF, DESIGN_FLOW_LPM)
                rows.append({
                    "campaign": "A1",
                    "topology": topo,
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "inlet_pressure_mpa": P_REF,
                    "defect_count": dc,
                    "bead_height_mm": bead,
                    "terminal_mpa": r["terminal_mpa"],
                    "pressure_margin_mpa": r["terminal_mpa"] - PASS_THRESHOLD_MPA,
                    "pass_fail": "PASS" if r["terminal_mpa"] >= PASS_THRESHOLD_MPA else "FAIL",
                    "loss_pipe_mpa": r["loss_pipe_mpa"],
                    "loss_fitting_mpa": r["loss_fitting_mpa"],
                    "loss_bead_mpa": r["loss_bead_mpa"],
                    "cross_main_loss_mpa": r["cross_main_loss_mpa"],
                    "tee_split_loss_mpa": r["tee_split_loss_mpa"],
                    "equipment_loss_mpa": r["equipment_loss_mpa"],
                })
                done += 1
                if done % 14 == 0:
                    print(f"    {done}/{total} 완료 ({elapsed(t0)})")

    df = pd.DataFrame(rows)
    save_csv(df, "A1_design_flow_deterministic.csv")
    print(f"  A-1 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign A-2: 유량 sweep 결정론 비교
# ══════════════════════════════════════════════════════════

def run_campaign_A2():
    print("\n" + "=" * 60)
    print("  Campaign A-2: 유량 sweep 결정론 비교 (420건)")
    print("=" * 60)
    t0 = time.time()
    rows = []
    total = len(TOPOS) * len(FLOW_TREND) * len(BEAD_DET) * len(DEFECT_COUNTS)
    done = 0

    for topo in TOPOS:
        for flow in FLOW_TREND:
            for bead in BEAD_DET:
                for dc in DEFECT_COUNTS:
                    r = run_det(topo, dc, bead, P_REF, flow)
                    rows.append({
                        "campaign": "A2",
                        "topology": topo,
                        "total_flow_lpm": flow,
                        "inlet_pressure_mpa": P_REF,
                        "defect_count": dc,
                        "bead_height_mm": bead,
                        "terminal_mpa": r["terminal_mpa"],
                        "pass_fail": "PASS" if r["terminal_mpa"] >= PASS_THRESHOLD_MPA else "FAIL",
                        "loss_pipe_mpa": r["loss_pipe_mpa"],
                        "loss_fitting_mpa": r["loss_fitting_mpa"],
                        "loss_bead_mpa": r["loss_bead_mpa"],
                        "cross_main_loss_mpa": r["cross_main_loss_mpa"],
                        "tee_split_loss_mpa": r["tee_split_loss_mpa"],
                        "equipment_loss_mpa": r["equipment_loss_mpa"],
                    })
                    done += 1
                    if done % 70 == 0:
                        print(f"    {done}/{total} 완료 ({elapsed(t0)})")

    df = pd.DataFrame(rows)
    save_csv(df, "A2_flow_sweep_deterministic.csv")
    print(f"  A-2 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign B-1: 설계유량 임계압력 비교
# ══════════════════════════════════════════════════════════

def run_campaign_B1():
    print("\n" + "=" * 60)
    print("  Campaign B-1: 설계유량 임계압력 비교 (70건)")
    print("=" * 60)
    t0 = time.time()
    rows = []
    total = len(TOPOS) * len(BEAD_DET) * len(DEFECT_COUNTS)
    done = 0

    for topo in TOPOS:
        for bead in BEAD_DET:
            for dc in DEFECT_COUNTS:
                p_crit = bisect_critical(topo, dc, bead, DESIGN_FLOW_LPM)
                r = run_det(topo, dc, bead, p_crit, DESIGN_FLOW_LPM)
                rows.append({
                    "campaign": "B1",
                    "topology": topo,
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "defect_count": dc,
                    "bead_height_mm": bead,
                    "critical_inlet_mpa": round(p_crit, 6),
                    "terminal_at_critical_mpa": r["terminal_mpa"],
                    "loss_pipe_mpa": r["loss_pipe_mpa"],
                    "loss_fitting_mpa": r["loss_fitting_mpa"],
                    "loss_bead_mpa": r["loss_bead_mpa"],
                    "cross_main_loss_mpa": r["cross_main_loss_mpa"],
                    "tee_split_loss_mpa": r["tee_split_loss_mpa"],
                    "equipment_loss_mpa": r["equipment_loss_mpa"],
                })
                done += 1
                if done % 10 == 0:
                    print(f"    {done}/{total} 완료 ({elapsed(t0)})")

    df = pd.DataFrame(rows)
    save_csv(df, "B1_design_flow_critical_pressure.csv")
    print(f"  B-1 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign B-2: 유량-비드 임계압력 맵
# ══════════════════════════════════════════════════════════

def run_campaign_B2():
    _b2_half = round(DESIGN_FLOW_LPM / 8 / 80) * 80
    b2_flows = list(range(int(DESIGN_FLOW_LPM) - _b2_half,
                          int(DESIGN_FLOW_LPM) + _b2_half + 1, 80))
    b2_defects = [0, 2, 4]
    print("\n" + "=" * 60)
    total = len(TOPOS) * len(b2_flows) * len(BEAD_DET) * len(b2_defects)
    print(f"  Campaign B-2: 유량-비드 임계압력 맵 ({total}건)")
    print("=" * 60)
    t0 = time.time()
    rows = []
    done = 0

    for topo in TOPOS:
        for flow in b2_flows:
            for bead in BEAD_DET:
                for dc in b2_defects:
                    p_crit = bisect_critical(topo, dc, bead, flow)
                    rows.append({
                        "campaign": "B2",
                        "topology": topo,
                        "total_flow_lpm": flow,
                        "defect_count": dc,
                        "bead_height_mm": bead,
                        "critical_inlet_mpa": round(p_crit, 6),
                    })
                    done += 1
                    if done % 42 == 0:
                        print(f"    {done}/{total} 완료 ({elapsed(t0)})")

    df = pd.DataFrame(rows)
    save_csv(df, "B2_flow_bead_critical_map.csv")
    print(f"  B-2 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign C-2: 기준선 (결함 없음, 결정론)
# ══════════════════════════════════════════════════════════

def run_campaign_C2():
    print("\n" + "=" * 60)
    print("  Campaign C-2: 기준선 (28건)")
    print("=" * 60)
    t0 = time.time()
    rows = []

    for topo in TOPOS:
        for p_in in PIN_TRANS:
            r = run_det(topo, 0, 0.0, p_in, DESIGN_FLOW_LPM)
            rows.append({
                "campaign": "C2",
                "topology": topo,
                "total_flow_lpm": DESIGN_FLOW_LPM,
                "inlet_pressure_mpa": p_in,
                "defect_count": 0,
                "bead_height_mm": 0.0,
                "bead_height_std_mm": 0.0,
                "terminal_mpa": r["terminal_mpa"],
                "pass_fail": "PASS" if r["terminal_mpa"] >= PASS_THRESHOLD_MPA else "FAIL",
            })

    df = pd.DataFrame(rows)
    save_csv(df, "C2_baseline_pressure_transition.csv")
    print(f"  C-2 완료: {len(rows)}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign C-1: 입구압 전이구간 신뢰성 (MC)
# ══════════════════════════════════════════════════════════

def run_campaign_C1(mc_iter=MC_ITER):
    c1_defects = [1, 2, 3, 4]
    total = len(TOPOS) * len(BEAD_MC) * len(BEAD_STD) * len(c1_defects) * len(PIN_TRANS)
    print("\n" + "=" * 60)
    print(f"  Campaign C-1: 입구압 전이구간 신뢰성 ({total}건 × MC {mc_iter})")
    print("=" * 60)
    t0 = time.time()
    rows = []
    done = 0

    for topo in TOPOS:
        for bead_mm in BEAD_MC:
            for bead_std in BEAD_STD:
                for dc in c1_defects:
                    for p_in in PIN_TRANS:
                        r = run_mc(topo, dc, bead_mm, bead_std,
                                   p_in, DESIGN_FLOW_LPM, mc_iter)
                        rows.append({
                            "campaign": "C1",
                            "topology": topo,
                            "total_flow_lpm": DESIGN_FLOW_LPM,
                            "inlet_pressure_mpa": p_in,
                            "defect_count": dc,
                            "bead_height_mm": bead_mm,
                            "bead_height_std_mm": bead_std,
                            "mc_iterations": mc_iter,
                            **r,
                        })
                        done += 1
                        if done % 24 == 0:
                            pct = done / total * 100
                            print(f"    {done}/{total} ({pct:.0f}%) "
                                  f"완료 ({elapsed(t0)})")

    df = pd.DataFrame(rows)
    save_csv(df, "C1_reliability_pressure_transition.csv")
    print(f"  C-1 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign D-1: 유량 전이구간 신뢰성 (MC)
# ══════════════════════════════════════════════════════════

def run_campaign_D1(mc_iter=MC_ITER):
    d1_defects = [1, 2, 3, 4]
    total = len(TOPOS) * len(FLOW_TRANS) * len(BEAD_MC) * len(BEAD_STD) * len(d1_defects)
    print("\n" + "=" * 60)
    print(f"  Campaign D-1: 유량 전이구간 신뢰성 ({total}건 × MC {mc_iter})")
    print("=" * 60)
    t0 = time.time()
    rows = []
    done = 0

    for topo in TOPOS:
        for flow in FLOW_TRANS:
            for bead_mm in BEAD_MC:
                for bead_std in BEAD_STD:
                    for dc in d1_defects:
                        r = run_mc(topo, dc, bead_mm, bead_std,
                                   P_REF, flow, mc_iter)
                        rows.append({
                            "campaign": "D1",
                            "topology": topo,
                            "total_flow_lpm": flow,
                            "inlet_pressure_mpa": P_REF,
                            "defect_count": dc,
                            "bead_height_mm": bead_mm,
                            "bead_height_std_mm": bead_std,
                            "mc_iterations": mc_iter,
                            **r,
                        })
                        done += 1
                        if done % 24 == 0:
                            pct = done / total * 100
                            print(f"    {done}/{total} ({pct:.0f}%) "
                                  f"완료 ({elapsed(t0)})")

    df = pd.DataFrame(rows)
    save_csv(df, "D1_reliability_flow_transition.csv")
    print(f"  D-1 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Campaign E-1: 위치 민감도
# ══════════════════════════════════════════════════════════

def run_campaign_E1():
    e1_beads = [1.0, 1.5, 2.0]
    total = len(TOPOS) * len(e1_beads) * HEADS_PER_BRANCH
    print("\n" + "=" * 60)
    print(f"  Campaign E-1: 위치 민감도 ({total}건)")
    print("=" * 60)
    t0 = time.time()
    rows = []

    # 기준선 (결함 없음)
    baseline = {}
    for topo in TOPOS:
        r = run_det(topo, 0, 0.0, P_REF, DESIGN_FLOW_LPM)
        baseline[topo] = r["terminal_mpa"]

    for topo in TOPOS:
        for bead_mm in e1_beads:
            for pos in range(HEADS_PER_BRANCH):
                t_mpa = run_det_at_position(topo, pos, bead_mm, P_REF, DESIGN_FLOW_LPM)
                rows.append({
                    "campaign": "E1",
                    "topology": topo,
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "inlet_pressure_mpa": P_REF,
                    "defect_count": 1,
                    "bead_height_mm": bead_mm,
                    "head_position": pos + 1,  # 1=입구측, 8=말단측
                    "terminal_mpa": t_mpa,
                    "baseline_terminal_mpa": baseline[topo],
                    "delta_mpa": t_mpa - baseline[topo],
                })

    df = pd.DataFrame(rows)
    save_csv(df, "E1_position_sensitivity.csv")
    print(f"  E-1 완료: {total}건, {elapsed(t0)}")
    return df


# ══════════════════════════════════════════════════════════
#  Manifest 저장
# ══════════════════════════════════════════════════════════

def save_manifest():
    manifest = {
        "script": "run_uni_bi_final.py",
        "run_date": datetime.now().isoformat(),
        "description": "단방향 vs 양방향 비교 — 토폴로지 교정 (uni:이경엘보K=0.53, bi:T분기K=1.0)",
        "defect_placement": "worst_branch_concentrated (positions 0~N-1)",
        "constants": {
            "K_TEE_BRANCH_80A": K_TEE_BRANCH,
            "K3_bi": K3,
            "K_ELBOW_90_65A_uni": K_ELBOW_90_65A,
            "K2": K2,
            "K1_BASE": K1_BASE,
            "USE_HEAD_FITTING": USE_HEAD_FITTING,
            "REDUCER_MODE": REDUCER_MODE,
            "HEADS_PER_BRANCH": HEADS_PER_BRANCH,
            "HEAD_SPACING_M": HEAD_SPACING_M,
            "BRANCH_SPACING_M": BRANCH_SPACING_M,
            "BRANCH_INLET_CONFIG_BI": BRANCH_INLET_CONFIG,
            "BRANCH_INLET_CONFIG_UNI": "80A-65A-elbow",
            "SUPPLY_PIPE_SIZE": SUPPLY_PIPE_SIZE,
            "PASS_THRESHOLD_MPA": PASS_THRESHOLD_MPA,
            "P_REF": P_REF,
            "P_REF_basis": "bisect(uni, dc=0, bead=0, flow=DESIGN_FLOW_LPM) -> terminal=0.1 MPa",
            "ACTIVE_HEADS": ACTIVE_HEADS,
            "DESIGN_FLOW_LPM": DESIGN_FLOW_LPM,
            "MC_ITER": MC_ITER,
            "EPSILON_MM": constants.EPSILON_MM,
            "RHO": constants.RHO,
            "MU": constants.MU,
            "NU": constants.NU,
        },
        "equipment_k_factors": {
            k: v for k, v in EQUIPMENT_K_FACTORS.items()
        },
    }
    # git revision
    try:
        import subprocess
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        manifest["git_revision"] = rev
    except Exception:
        manifest["git_revision"] = "unknown"

    path = OUT_DIR / "run_manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  Manifest 저장: {path}")
    return manifest


# ══════════════════════════════════════════════════════════
#  Smoke Test
# ══════════════════════════════════════════════════════════

def run_smoke_test():
    print("\n" + "=" * 60)
    print("  Smoke Test (4건)")
    print("=" * 60)
    results = []
    passed = True

    # Test 1: uni, bead=0, defect=0
    r1 = run_det("uni", 0, 0.0, P_REF, DESIGN_FLOW_LPM)
    ok1 = abs(r1["terminal_mpa"] - 0.1) < 0.005
    results.append(("uni/bead=0/dc=0", r1["terminal_mpa"], "~0.1000", ok1))

    # Test 2: bi, bead=0, defect=0 → terminal < uni
    r2 = run_det("bi", 0, 0.0, P_REF, DESIGN_FLOW_LPM)
    ok2 = r2["terminal_mpa"] < r1["terminal_mpa"]
    results.append(("bi/bead=0/dc=0", r2["terminal_mpa"], f"< {r1['terminal_mpa']:.4f}", ok2))

    # Test 3: uni, bead=1.5, defect=2 → terminal < case 1
    r3 = run_det("uni", 2, 1.5, P_REF, DESIGN_FLOW_LPM)
    ok3 = r3["terminal_mpa"] < r1["terminal_mpa"]
    results.append(("uni/bead=1.5/dc=2", r3["terminal_mpa"], f"< {r1['terminal_mpa']:.4f}", ok3))

    # Test 4: bi, bead=1.5, defect=2 → tee_loss > 0
    r4 = run_det("bi", 2, 1.5, P_REF, DESIGN_FLOW_LPM)
    ok4 = r4["tee_split_loss_mpa"] > 0
    results.append(("bi/bead=1.5/dc=2", r4["tee_split_loss_mpa"], "> 0", ok4))

    # K=1.06 검증: tee_loss 역산
    expected_tee = calc_tee_split_loss_mpa(DESIGN_FLOW_LPM)
    ok_k = abs(r4["tee_split_loss_mpa"] - expected_tee) < 0.0001
    results.append(("K=1.06 tee_loss", r4["tee_split_loss_mpa"],
                     f"~{expected_tee:.6f}", ok_k))

    # 토폴로지별 K값 검증
    results.append(("K_ELBOW_90_65A (uni)", K_ELBOW_90_65A, "= 0.53", abs(K_ELBOW_90_65A - 0.53) < 0.01))
    results.append(("K3 (bi)", K3, "= 1.0", abs(K3 - 1.0) < 0.01))

    # 설정값 기록
    ok_hs = HEAD_SPACING_M > 0
    results.append(("HEAD_SPACING_M", HEAD_SPACING_M, "> 0", ok_hs))
    results.append(("HEADS_PER_BRANCH", HEADS_PER_BRANCH, f"= {HEADS_PER_BRANCH}", True))
    results.append(("DESIGN_FLOW_LPM", DESIGN_FLOW_LPM, f"= {DESIGN_FLOW_LPM}", True))

    print()
    for name, val, expect, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {val} (기대: {expect})")
        if not ok:
            passed = False

    # 로그 저장
    with open(OUT_DIR / "smoke_test_log.txt", "w", encoding="utf-8") as f:
        f.write(f"Smoke Test - {datetime.now().isoformat()}\n")
        f.write(f"K_TEE_BRANCH = {K_TEE_BRANCH}\n")
        f.write(f"K_ELBOW_90_65A = {K_ELBOW_90_65A} (uni 분기 입구)\n")
        f.write(f"K3 = {K3} (bi 분기 입구)\n")
        f.write(f"BRANCH_INLET: uni=80A-65A-elbow, bi={BRANCH_INLET_CONFIG}\n")
        f.write(f"HEAD_SPACING_M = {HEAD_SPACING_M}\n\n")
        for name, val, expect, ok in results:
            f.write(f"[{'PASS' if ok else 'FAIL'}] {name}: {val} (기대: {expect})\n")

    if not passed:
        print("\n  *** SMOKE TEST 실패 — 실행 중단 ***")
        sys.exit(1)
    else:
        print(f"\n  Smoke Test 전체 PASS")
    return passed


# ══════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="단방향 vs 양방향 비교 — 토폴로지 교정본"
    )
    parser.add_argument("--case", type=str,
                        help="캠페인 선택: A1, A2, B1, B2, C1, C2, D1, E1, ALL")
    parser.add_argument("--smoke", action="store_true",
                        help="Smoke test만 실행")
    parser.add_argument("--mc", type=int, default=MC_ITER,
                        help=f"MC 반복 횟수 (기본: {MC_ITER})")
    parser.add_argument("--heads", type=int, default=None,
                        help="가지배관당 헤드 수 (기본: 8)")
    parser.add_argument("--spacing", type=float, default=None,
                        help="헤드 간격 m (기본: 2.1)")
    parser.add_argument("--outdir", type=str, default=None,
                        help="출력 디렉토리 (기본: uni_bi_simulation_results)")
    args = parser.parse_args()

    # ── global 재할당 ──────────────────────────────────────
    global HEADS_PER_BRANCH, HEAD_SPACING_M, ACTIVE_HEADS, DESIGN_FLOW_LPM
    global OUT_DIR, DATA_DIR, FIG_DIR
    global P_REF, PIN_TRANS, FLOW_TRANS, FLOW_TREND

    if args.heads is not None:
        HEADS_PER_BRANCH = args.heads
    if args.spacing is not None:
        HEAD_SPACING_M = args.spacing
    if args.outdir is not None:
        OUT_DIR = Path(args.outdir)
        DATA_DIR = OUT_DIR / "data"
        FIG_DIR = OUT_DIR / "figures"

    ACTIVE_HEADS = NUM_BRANCHES * HEADS_PER_BRANCH
    DESIGN_FLOW_LPM = ACTIVE_HEADS * HEAD_FLOW_LPM

    # 동적 범위 재계산
    _step_trend = round(DESIGN_FLOW_LPM / 8)
    FLOW_TREND = list(range(int(DESIGN_FLOW_LPM - 3 * _step_trend),
                            int(DESIGN_FLOW_LPM + 3 * _step_trend),
                            int(_step_trend)))
    _fstep = max(40, round(DESIGN_FLOW_LPM * 0.016 / 10) * 10)
    FLOW_TRANS = list(range(int(DESIGN_FLOW_LPM - 5 * _fstep),
                            int(DESIGN_FLOW_LPM + 5 * _fstep),
                            int(_fstep)))

    # P_REF: 단방향(uni), 결함=0, 비드=0에서 말단=0.1 MPa 되는 최소 입구압 역산
    P_REF = bisect_critical("uni", 0, 0.0, DESIGN_FLOW_LPM, tol=0.000001)
    P_REF = round(P_REF, 6)

    # 입구압 전이구간: P_REF 중심으로 동적 생성
    _p_lo = round((P_REF - 0.005) / 0.002) * 0.002
    PIN_TRANS = [round(_p_lo + i * 0.002, 3) for i in range(14)]

    ensure_dirs()
    manifest = save_manifest()

    print("\n" + "=" * 60)
    print("  FiPLSim 단방향 vs 양방향 비교 — NFPA 13 최종본")
    print("=" * 60)
    print(f"  K_TEE_BRANCH = {K_TEE_BRANCH}")
    print(f"  K_ELBOW_90_65A = {K_ELBOW_90_65A} (uni 분기)")
    print(f"  K3 = {K3} (bi 분기)")
    print(f"  BRANCH_INLET: uni=80A-65A-elbow, bi={BRANCH_INLET_CONFIG}")
    print(f"  HEADS_PER_BRANCH = {HEADS_PER_BRANCH}")
    print(f"  HEAD_SPACING = {HEAD_SPACING_M} m")
    print(f"  DESIGN_FLOW = {DESIGN_FLOW_LPM} LPM ({ACTIVE_HEADS}헤드)")
    print(f"  P_REF = {P_REF} MPa (uni 기준 bisect 역산)")
    print(f"  MC = {args.mc}")
    print(f"  출력: {OUT_DIR}/")

    if args.smoke:
        run_smoke_test()
        return

    # Smoke test 먼저
    run_smoke_test()

    case = (args.case or "ALL").upper()
    t_total = time.time()

    campaigns = {
        "A1": run_campaign_A1,
        "A2": run_campaign_A2,
        "B1": run_campaign_B1,
        "B2": run_campaign_B2,
        "C2": run_campaign_C2,
        "C1": lambda: run_campaign_C1(args.mc),
        "D1": lambda: run_campaign_D1(args.mc),
        "E1": run_campaign_E1,
    }

    run_order = ["A1", "B1", "C2", "C1", "A2", "B2", "D1", "E1"]

    if case == "ALL":
        for key in run_order:
            campaigns[key]()
    elif case in campaigns:
        campaigns[case]()
    else:
        print(f"  알 수 없는 캠페인: {case}")
        print(f"  사용 가능: {', '.join(run_order)} 또는 ALL")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print(f"  전체 완료: {elapsed(t_total)}")
    print(f"  결과: {OUT_DIR}/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
