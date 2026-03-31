#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim vs EPANET 독립 검증 스크립트
=====================================
FiPLSim 자체 수리 해석 엔진의 신뢰성을 검증하기 위해,
미국 EPA의 EPANET 솔버와 동일 조건에서 비교합니다.

- EPANET 솔버: EPyT (Python wrapper for EPANET 2.2)
- 마찰 모델: Darcy-Weisbach + Colebrook-White (양쪽 동일)
- 검증 케이스: 9건 (Baseline 5건 + Bead 등가 4건)
- 출력: CSV 3개, 그래프 4개, .inp 파일 9개

실행:
  PYTHONIOENCODING=utf-8 python3 run_epanet_validation.py
"""

import sys
import os
import math
import importlib
import warnings
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# ── 프로젝트 루트를 sys.path에 추가 (paper/scripts/ → 루트) ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ──────────────────────────────────────────────
# 논문용 물성치 오버라이드 (run_sim_v2.py와 동일)
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

EQUIPMENT_K_FACTORS = {
    "알람밸브 (습식)":     {"K": 2.0,  "qty": 1},
    "유수검지장치":        {"K": 1.0,  "qty": 1},
    "게이트밸브 (전개)":   {"K": 0.15, "qty": 2},
    "체크밸브 (스윙형)":   {"K": 2.0,  "qty": 1},
    "90° 엘보":           {"K": 0.75, "qty": 1},
    "리듀서 (점축소)":     {"K": 0.15, "qty": 1},
}
EQUIP_K_TOTAL = sum(v["K"] * v.get("qty", 1) for v in EQUIPMENT_K_FACTORS.values())

# 출력 디렉토리
BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "epanet_validation"
DATA_DIR = OUTPUT_DIR / "data"
FIG_DIR = OUTPUT_DIR / "figures"
INP_DIR = OUTPUT_DIR / "inp"

# 물성치
RHO = constants.RHO
G = constants.G
NU = constants.NU
EPSILON_M = constants.EPSILON_M

# ══════════════════════════════════════════════
#  유틸리티
# ══════════════════════════════════════════════

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    INP_DIR.mkdir(parents=True, exist_ok=True)


def mpa_to_head(p_mpa):
    return p_mpa * 1e6 / (RHO * G)


def head_to_mpa(h_m):
    return RHO * G * h_m / 1e6


def gen_params():
    return dict(
        inlet_pressure_mpa=0.0,  # placeholder
        total_flow_lpm=TOTAL_FLOW_LPM,
        num_branches=NUM_BRANCHES,
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        branch_inlet_config=BRANCH_INLET_CONFIG,
        use_head_fitting=USE_HEAD_FITTING,
    )


def common_params():
    return dict(
        equipment_k_factors=EQUIPMENT_K_FACTORS,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
        reducer_mode=REDUCER_MODE,
    )


def get_pipe_id_mm(nominal):
    return constants.PIPE_DIMENSIONS[nominal]["id_mm"]


def get_pipe_id_m(nominal):
    return get_pipe_id_mm(nominal) / 1000.0


# ══════════════════════════════════════════════
#  EPANET .inp 파일 생성
# ══════════════════════════════════════════════

def build_inp_file(case_name, inlet_pressure_mpa, bead_height_mm=0.0):
    """
    FiPLSim 배관망을 EPANET .inp 텍스트로 변환합니다.

    노드 구성:
      R1         — 저수조 (입구압 = total head)
      J_equip    — 장비 손실 후
      J_cm0~3    — 교차배관 분기점 ×4
      J_b{b}_in  — 가지배관 입구 ×4
      J_b{b}_rd  — 레듀서 후 ×4
      J_b{b}_h{h} — 헤드 위치 ×32

    배관 구성:
      P_supply   — R1→J_equip (100A, K=EQUIP_K_TOTAL)
      P_cm0      — J_equip→J_cm0 (80A, 더미)
      P_cm1~3    — J_cm{i-1}→J_cm{i} (80A, 3.5m, K=TEE_RUN)
      P_b{b}_k3  — J_cm{b}→J_b{b}_in (65A, 0.3m, K=K3)
      P_b{b}_red — J_b{b}_in→J_b{b}_rd (50A, 더미, K=K_red_65_50)
      P_b{b}_h{h} — 헤드 구간 (관경별, HEAD_SPACING_M, K=K1+K2+K_red)
    """
    head_flow_lps = (TOTAL_FLOW_LPM / ACTIVE_HEADS) / 60.0  # LPM→LPS
    inlet_head_m = mpa_to_head(inlet_pressure_mpa)

    # K값 계산
    K1_BASE = constants.K1_BASE  # 0.5
    K2_val = constants.K2 if USE_HEAD_FITTING else constants.K2_WITHOUT_HEAD_FITTING
    K3_val = constants.K3  # 1.0
    K_TEE = constants.K_TEE_RUN  # 0.3

    # 비드 K1 계산 (관경별)
    def calc_K1(nominal, bead_mm):
        id_mm = get_pipe_id_mm(nominal)
        return hydraulics.k_welded_fitting(bead_mm, id_mm, K1_BASE)

    # 레듀서 K (Crane TP-410)
    def calc_K_red(up_nom, dn_nom):
        d1 = get_pipe_id_mm(up_nom)
        d2 = get_pipe_id_mm(dn_nom)
        theta = constants.REDUCER_ANGLES_DEG.get((up_nom, dn_nom), 10.0)
        return hydraulics.k_reducer(d1, d2, theta, REDUCER_MODE)

    # 가지배관 관경 배열 (8헤드: 50A×3, 40A×2, 32A×1, 25A×2)
    pipe_sizes = []
    for h in range(HEADS_PER_BRANCH):
        n_downstream = HEADS_PER_BRANCH - h
        pipe_sizes.append(constants.auto_pipe_size(n_downstream))

    # ── 노드 정의 ──
    junctions = []  # (id, elev, demand)
    junctions.append(("J_equip", 0.0, 0.0))
    for b in range(NUM_BRANCHES):
        junctions.append((f"J_cm{b}", 0.0, 0.0))
        junctions.append((f"J_b{b}_in", 0.0, 0.0))
        # 입구관이 첫 헤드 관경과 다르면 레듀서 노드 추가
        if "65A" != pipe_sizes[0]:  # 65A→50A 레듀서
            junctions.append((f"J_b{b}_rd", 0.0, 0.0))
        for h in range(HEADS_PER_BRANCH):
            junctions.append((f"J_b{b}_h{h}", 0.0, head_flow_lps))

    # ── 배관 정의 ──
    pipes = []  # (id, node1, node2, length, diameter_mm, roughness_mm, minor_loss)

    # 공급관: R1→J_equip (100A, 더미길이, K=EQUIP_K_TOTAL)
    pipes.append(("P_supply", "R1", "J_equip",
                   0.01, get_pipe_id_mm("100A"), constants.EPSILON_MM, EQUIP_K_TOTAL))

    # 교차배관 시작: J_equip→J_cm0 (80A, 더미)
    pipes.append(("P_cm0", "J_equip", "J_cm0",
                   0.01, get_pipe_id_mm("80A"), constants.EPSILON_MM, 0.0))

    # 교차배관 직진: J_cm{i-1}→J_cm{i} (80A, 3.5m, K=TEE_RUN)
    for i in range(1, NUM_BRANCHES):
        pipes.append((f"P_cm{i}", f"J_cm{i-1}", f"J_cm{i}",
                       BRANCH_SPACING_M, get_pipe_id_mm("80A"), constants.EPSILON_MM, K_TEE))

    # 각 가지배관
    for b in range(NUM_BRANCHES):
        branch_flow = TOTAL_FLOW_LPM / NUM_BRANCHES  # 640 LPM

        # K3 분기 입구: J_cm{b}→J_b{b}_in (65A, 0.3m, K=K3)
        inlet_pipe = "65A"
        pipes.append((f"P_b{b}_k3", f"J_cm{b}", f"J_b{b}_in",
                       0.3, get_pipe_id_mm(inlet_pipe), constants.EPSILON_MM, K3_val))

        # 입구 레듀서: J_b{b}_in→J_b{b}_rd (50A, 더미, K=K_red_65→50)
        first_head_size = pipe_sizes[0]  # 50A
        if inlet_pipe != first_head_size:
            K_red_inlet = calc_K_red(inlet_pipe, first_head_size)
            pipes.append((f"P_b{b}_red", f"J_b{b}_in", f"J_b{b}_rd",
                           0.01, get_pipe_id_mm(first_head_size), constants.EPSILON_MM, K_red_inlet))
            prev_node = f"J_b{b}_rd"
        else:
            prev_node = f"J_b{b}_in"

        # 헤드 구간
        for h in range(HEADS_PER_BRANCH):
            nom = pipe_sizes[h]
            node_id = f"J_b{b}_h{h}"

            # K1(비드) + K2(헤드이음쇠)
            K1 = calc_K1(nom, bead_height_mm)
            K_total = K1 + K2_val

            # 관경 전환 레듀서 (h>0이고 이전 관경과 다를 때)
            if h > 0 and pipe_sizes[h-1] != nom:
                K_red = calc_K_red(pipe_sizes[h-1], nom)
                K_total += K_red

            pipes.append((f"P_b{b}_h{h}", prev_node, node_id,
                           HEAD_SPACING_M, get_pipe_id_mm(nom), constants.EPSILON_MM, K_total))
            prev_node = node_id

    # ── .inp 텍스트 생성 ──
    lines = []
    lines.append("[TITLE]")
    lines.append(f"FiPLSim Validation — {case_name}")
    lines.append(f"; P_inlet={inlet_pressure_mpa} MPa, bead={bead_height_mm} mm")
    lines.append("")

    lines.append("[JUNCTIONS]")
    lines.append(";ID              \tElev\tDemand")
    for jid, elev, demand in junctions:
        lines.append(f"{jid:<16s}\t{elev:.2f}\t{demand:.6f}")
    lines.append("")

    lines.append("[RESERVOIRS]")
    lines.append(";ID\tHead")
    lines.append(f"R1\t{inlet_head_m:.6f}")
    lines.append("")

    lines.append("[TANKS]")
    lines.append("")

    lines.append("[PIPES]")
    lines.append(";ID              \tNode1           \tNode2           \tLength\tDiameter\tRoughness\tMinorLoss\tStatus")
    for pid, n1, n2, length, diam, rough, mloss in pipes:
        lines.append(f"{pid:<16s}\t{n1:<16s}\t{n2:<16s}\t{length:.4f}\t{diam:.4f}\t{rough:.6f}\t{mloss:.6f}\tOpen")
    lines.append("")

    lines.append("[PUMPS]")
    lines.append("")
    lines.append("[VALVES]")
    lines.append("")
    lines.append("[EMITTERS]")
    lines.append("")
    lines.append("[CURVES]")
    lines.append("")
    lines.append("[PATTERNS]")
    lines.append("")
    lines.append("[ENERGY]")
    lines.append("")
    lines.append("[STATUS]")
    lines.append("")
    lines.append("[RULES]")
    lines.append("")
    lines.append("[DEMANDS]")
    lines.append("")

    lines.append("[OPTIONS]")
    lines.append("UNITS              \tLPS")
    lines.append("HEADLOSS           \tD-W")
    lines.append(f"VISCOSITY          \t{NU * 1e6:.6f}")
    lines.append(f"SPECIFIC GRAVITY   \t{RHO / 998.0:.6f}")
    lines.append("TRIALS             \t200")
    lines.append("ACCURACY           \t0.000001")
    lines.append("UNBALANCED         \tCONTINUE 100")
    lines.append("")

    lines.append("[TIMES]")
    lines.append("DURATION           \t0:00")
    lines.append("HYDRAULIC TIMESTEP \t0:05")
    lines.append("REPORT TIMESTEP    \t0:05")
    lines.append("")

    lines.append("[REPORT]")
    lines.append("STATUS             \tNO")
    lines.append("SUMMARY            \tNO")
    lines.append("NODES              \tALL")
    lines.append("LINKS              \tALL")
    lines.append("")

    # 좌표 (경고 방지용 더미)
    lines.append("[COORDINATES]")
    x = 0.0
    lines.append(f"R1\t{x:.2f}\t0.00")
    x += 1.0
    lines.append(f"J_equip\t{x:.2f}\t0.00")
    x += 1.0
    for b in range(NUM_BRANCHES):
        cx = x + b * BRANCH_SPACING_M
        lines.append(f"J_cm{b}\t{cx:.2f}\t0.00")
        lines.append(f"J_b{b}_in\t{cx:.2f}\t{-1.0:.2f}")
        if inlet_pipe != first_head_size:
            lines.append(f"J_b{b}_rd\t{cx:.2f}\t{-2.0:.2f}")
        for h in range(HEADS_PER_BRANCH):
            lines.append(f"J_b{b}_h{h}\t{cx:.2f}\t{-(3.0 + h):.2f}")
    lines.append("")

    lines.append("[VERTICES]")
    lines.append("")
    lines.append("[LABELS]")
    lines.append("")
    lines.append("[BACKDROP]")
    lines.append("")

    lines.append("[END]")

    return "\n".join(lines)


# ══════════════════════════════════════════════
#  EPANET 실행 + 결과 추출
# ══════════════════════════════════════════════

def run_epanet_case(case_name, inlet_pressure_mpa, bead_height_mm=0.0):
    """단일 케이스 EPANET 실행 → 32개 헤드 노드 압력 반환"""
    inp_text = build_inp_file(case_name, inlet_pressure_mpa, bead_height_mm)

    # .inp 파일 저장 (재현성용)
    inp_path = INP_DIR / f"{case_name}.inp"
    with open(inp_path, "w", encoding="utf-8") as f:
        f.write(inp_text)

    # EPyT로 실행
    from epyt import epanet
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        d = epanet(str(inp_path))

    try:
        d.solveCompleteHydraulics()
        # 헤드 노드 압력 추출 (m → MPa)
        node_ids = d.getNodeNameID()
        node_pressures = d.getNodePressure()  # meters of head

        results = {}
        for b in range(NUM_BRANCHES):
            for h in range(HEADS_PER_BRANCH):
                nid = f"J_b{b}_h{h}"
                idx = node_ids.index(nid)
                p_m = node_pressures[idx]  # pressure in meters
                p_mpa = head_to_mpa(p_m)
                results[nid] = {"branch": b, "head": h, "pressure_m": p_m, "pressure_mpa": p_mpa}
        return results
    finally:
        d.unload()


# ══════════════════════════════════════════════
#  FiPLSim 기준값 계산
# ══════════════════════════════════════════════

def run_fiplsim_case(inlet_pressure_mpa, bead_height_mm=0.0):
    """FiPLSim으로 동일 조건 계산 → 32개 노드 압력 반환"""
    gp = gen_params()
    gp["inlet_pressure_mpa"] = inlet_pressure_mpa
    # bead_heights_2d: 4분기 × 8헤드 2D 리스트 (균일 비드)
    gp["bead_heights_2d"] = [[bead_height_mm] * HEADS_PER_BRANCH
                              for _ in range(NUM_BRANCHES)]

    system = pn.generate_dynamic_system(**gp)
    result = pn.calculate_dynamic_system(system, **common_params())

    pressures = {}
    for b in range(NUM_BRANCHES):
        profile = result["branch_profiles"][b]
        # pressures_mpa: [inlet, h0, h1, ..., h7] → h0~h7은 인덱스 1~8
        for h in range(HEADS_PER_BRANCH):
            nid = f"J_b{b}_h{h}"
            p_mpa = profile["pressures_mpa"][h + 1]
            pressures[nid] = {"branch": b, "head": h, "pressure_mpa": p_mpa}

    pressures["_meta"] = {
        "worst_branch_index": result["worst_branch_index"],
        "worst_terminal_mpa": result["worst_terminal_mpa"],
        "equipment_loss_mpa": result["equipment_loss_mpa"],
        "loss_pipe_mpa": result["loss_pipe_mpa"],
        "loss_fitting_mpa": result["loss_fitting_mpa"],
        "loss_bead_mpa": result["loss_bead_mpa"],
    }
    return pressures


# ══════════════════════════════════════════════
#  검증 케이스 정의
# ══════════════════════════════════════════════

CASES = [
    # Baseline (bead=0)
    {"name": "E-B0-1", "p_inlet": 0.5327, "bead": 0.0, "desc": "규정 경계 (P_ref≈0.5314)"},
    {"name": "E-B0-2", "p_inlet": 0.55,   "bead": 0.0, "desc": "경계 상방"},
    {"name": "E-B0-3", "p_inlet": 0.60,   "bead": 0.0, "desc": "설계 여유"},
    {"name": "E-B0-4", "p_inlet": 0.70,   "bead": 0.0, "desc": "중간 고압"},
    {"name": "E-B0-5", "p_inlet": 0.80,   "bead": 0.0, "desc": "고압 선형성"},
    # Bead 등가손실
    {"name": "E-B1-1", "p_inlet": 0.5465, "bead": 1.5, "desc": "1.5mm 임계압"},
    {"name": "E-B1-2", "p_inlet": 0.5684, "bead": 3.0, "desc": "3.0mm 임계압"},
    {"name": "E-B1-3", "p_inlet": 0.60,   "bead": 1.5, "desc": "여유압+1.5mm"},
    {"name": "E-B1-4", "p_inlet": 0.60,   "bead": 3.0, "desc": "여유압+3.0mm"},
]


# ══════════════════════════════════════════════
#  비교 실행
# ══════════════════════════════════════════════

def run_all_cases():
    """9개 케이스 모두 실행 → 비교 데이터 생성"""
    all_nodes = []      # 32노드 × 9케이스
    summary = []        # 케이스별 요약
    branch_terms = []   # 분기별 말단

    for case in CASES:
        name = case["name"]
        p_in = case["p_inlet"]
        bead = case["bead"]
        desc = case["desc"]
        print(f"  [{name}] P={p_in} MPa, bead={bead} mm ... ", end="", flush=True)

        # FiPLSim
        fip = run_fiplsim_case(p_in, bead)
        meta = fip.pop("_meta")

        # EPANET
        epa = run_epanet_case(name, p_in, bead)

        # 노드별 비교
        case_errors = []
        for b in range(NUM_BRANCHES):
            for h in range(HEADS_PER_BRANCH):
                nid = f"J_b{b}_h{h}"
                p_fip = fip[nid]["pressure_mpa"]
                p_epa = epa[nid]["pressure_mpa"]
                err = p_epa - p_fip
                err_pct = (err / p_fip * 100) if p_fip != 0 else 0.0

                all_nodes.append({
                    "case": name, "desc": desc,
                    "p_inlet_mpa": p_in, "bead_mm": bead,
                    "branch": b, "head": h, "node_id": nid,
                    "FiPLSim_mpa": round(p_fip, 6),
                    "EPANET_mpa": round(p_epa, 6),
                    "error_mpa": round(err, 6),
                    "error_pct": round(err_pct, 4),
                })
                case_errors.append(abs(err_pct))

                # 말단 노드 (h=7)
                if h == HEADS_PER_BRANCH - 1:
                    branch_terms.append({
                        "case": name, "p_inlet_mpa": p_in, "bead_mm": bead,
                        "branch": b,
                        "FiPLSim_terminal_mpa": round(p_fip, 6),
                        "EPANET_terminal_mpa": round(p_epa, 6),
                        "error_pct": round(err_pct, 4),
                    })

        # 최악 말단 (EPANET)
        epa_terminals = [epa[f"J_b{b}_h{HEADS_PER_BRANCH-1}"]["pressure_mpa"]
                         for b in range(NUM_BRANCHES)]
        epa_worst = min(epa_terminals)
        epa_worst_branch = epa_terminals.index(epa_worst)

        summary.append({
            "case": name, "desc": desc,
            "p_inlet_mpa": p_in, "bead_mm": bead,
            "FiPLSim_worst_mpa": round(meta["worst_terminal_mpa"], 6),
            "EPANET_worst_mpa": round(epa_worst, 6),
            "FiPLSim_worst_branch": meta["worst_branch_index"],
            "EPANET_worst_branch": epa_worst_branch,
            "same_worst_branch": meta["worst_branch_index"] == epa_worst_branch,
            "worst_error_pct": round(
                (epa_worst - meta["worst_terminal_mpa"]) / meta["worst_terminal_mpa"] * 100
                if meta["worst_terminal_mpa"] != 0 else 0.0, 4),
            "max_node_error_pct": round(max(case_errors), 4),
            "mean_node_error_pct": round(np.mean(case_errors), 4),
        })
        print(f"worst err={summary[-1]['worst_error_pct']:.3f}%, max node={summary[-1]['max_node_error_pct']:.3f}%")

    return all_nodes, summary, branch_terms


# ══════════════════════════════════════════════
#  CSV 출력
# ══════════════════════════════════════════════

def save_csvs(all_nodes, summary, branch_terms):
    df_all = pd.DataFrame(all_nodes)
    df_sum = pd.DataFrame(summary)
    df_bt = pd.DataFrame(branch_terms)

    df_all.to_csv(DATA_DIR / "comparison_all_nodes.csv", index=False, encoding="utf-8-sig")
    df_sum.to_csv(DATA_DIR / "comparison_summary.csv", index=False, encoding="utf-8-sig")
    df_bt.to_csv(DATA_DIR / "comparison_branch_terminals.csv", index=False, encoding="utf-8-sig")

    print(f"  CSV 저장 완료: {DATA_DIR}")
    return df_all, df_sum, df_bt


# ══════════════════════════════════════════════
#  그래프 출력
# ══════════════════════════════════════════════

def plot_parity(df_all):
    """1. Parity plot — FiPLSim vs EPANET (288 points)"""
    fig, ax = plt.subplots(figsize=(8, 8))
    colors = {"E-B0-1": "#1f77b4", "E-B0-2": "#ff7f0e", "E-B0-3": "#2ca02c",
              "E-B0-4": "#d62728", "E-B0-5": "#9467bd",
              "E-B1-1": "#8c564b", "E-B1-2": "#e377c2",
              "E-B1-3": "#7f7f7f", "E-B1-4": "#bcbd22"}

    for case_name, grp in df_all.groupby("case"):
        ax.scatter(grp["FiPLSim_mpa"], grp["EPANET_mpa"],
                   s=20, alpha=0.7, label=case_name, color=colors.get(case_name, "gray"))

    mn = min(df_all["FiPLSim_mpa"].min(), df_all["EPANET_mpa"].min()) * 0.95
    mx = max(df_all["FiPLSim_mpa"].max(), df_all["EPANET_mpa"].max()) * 1.05
    ax.plot([mn, mx], [mn, mx], "k--", lw=1, label="y = x (perfect)")
    ax.set_xlabel("FiPLSim Pressure (MPa)", fontsize=12)
    ax.set_ylabel("EPANET Pressure (MPa)", fontsize=12)
    ax.set_title("FiPLSim vs EPANET — Node Pressure Parity Plot", fontsize=14)
    ax.legend(fontsize=8, ncol=2)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "01_parity_plot.png", dpi=200)
    plt.close(fig)
    print("  [그래프 1/4] Parity plot 저장")


def plot_error_bar(df_sum):
    """2. Error bar chart — case별 최대/평균 오차"""
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(df_sum))
    w = 0.35

    ax.bar(x - w/2, df_sum["max_node_error_pct"], w, label="Max Node Error (%)", color="#d62728", alpha=0.8)
    ax.bar(x + w/2, df_sum["mean_node_error_pct"], w, label="Mean Node Error (%)", color="#1f77b4", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(df_sum["case"], rotation=45, ha="right")
    ax.set_ylabel("Error (%)", fontsize=12)
    ax.set_title("FiPLSim vs EPANET — Error by Case", fontsize=14)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "02_error_bar_chart.png", dpi=200)
    plt.close(fig)
    print("  [그래프 2/4] Error bar chart 저장")


def plot_pressure_profile(df_all):
    """3. Pressure profile overlay — Branch #3 (worst)"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Baseline case (E-B0-3, P=0.60)
    for ax_idx, case_name in enumerate(["E-B0-3", "E-B1-4"]):
        ax = axes[ax_idx]
        grp = df_all[df_all["case"] == case_name]

        for b in range(NUM_BRANCHES):
            bgrp = grp[grp["branch"] == b].sort_values("head")
            style = "-o" if b == 3 else "--"
            alpha = 1.0 if b == 3 else 0.4
            lw = 2 if b == 3 else 1

            ax.plot(bgrp["head"], bgrp["FiPLSim_mpa"], style, color=f"C{b}",
                    alpha=alpha, lw=lw, label=f"FiPLSim B#{b}" if b == 3 else None)
            ax.plot(bgrp["head"], bgrp["EPANET_mpa"], "x", color=f"C{b}",
                    ms=8, alpha=alpha, label=f"EPANET B#{b}" if b == 3 else None)

        ax.set_xlabel("Head Position", fontsize=11)
        ax.set_ylabel("Pressure (MPa)", fontsize=11)
        ax.set_title(f"Pressure Profile — {case_name}", fontsize=12)
        ax.axhline(0.1, color="red", ls=":", lw=1, label="PASS threshold (0.1 MPa)")
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "03_pressure_profile.png", dpi=200)
    plt.close(fig)
    print("  [그래프 3/4] Pressure profile 저장")


def plot_error_heatmap(df_all):
    """4. Node error heatmap — 4×8 grid (worst case)"""
    # Use E-B0-1 (규정 경계) for the heatmap
    grp = df_all[df_all["case"] == "E-B0-1"]
    err_grid = np.zeros((NUM_BRANCHES, HEADS_PER_BRANCH))
    for _, row in grp.iterrows():
        err_grid[int(row["branch"]), int(row["head"])] = row["error_pct"]

    fig, ax = plt.subplots(figsize=(10, 4))
    im = ax.imshow(np.abs(err_grid), cmap="YlOrRd", aspect="auto",
                   vmin=0, vmax=max(0.5, np.abs(err_grid).max()))
    ax.set_xlabel("Head Position (0–7)", fontsize=11)
    ax.set_ylabel("Branch (0–3)", fontsize=11)
    ax.set_title("Absolute Error (%) — Case E-B0-1 (P_ref boundary)", fontsize=13)
    ax.set_xticks(range(HEADS_PER_BRANCH))
    ax.set_yticks(range(NUM_BRANCHES))
    ax.set_yticklabels([f"B#{b}" for b in range(NUM_BRANCHES)])

    for b in range(NUM_BRANCHES):
        for h in range(HEADS_PER_BRANCH):
            ax.text(h, b, f"{abs(err_grid[b, h]):.3f}%",
                    ha="center", va="center", fontsize=8,
                    color="white" if abs(err_grid[b, h]) > 0.25 else "black")

    fig.colorbar(im, ax=ax, label="Error (%)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "04_error_heatmap.png", dpi=200)
    plt.close(fig)
    print("  [그래프 4/4] Error heatmap 저장")


# ══════════════════════════════════════════════
#  메인
# ══════════════════════════════════════════════

def main():
    print("=" * 60)
    print("FiPLSim vs EPANET 독립 검증")
    print(f"  날짜: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  케이스: {len(CASES)}건")
    print(f"  유량: {TOTAL_FLOW_LPM} LPM ({ACTIVE_HEADS}헤드 × 80 LPM)")
    print(f"  물성치: ρ={RHO}, ν={NU:.3e}, ε={constants.EPSILON_MM} mm")
    print("=" * 60)

    ensure_dirs()

    print("\n[Step 1] 9개 케이스 실행 중...")
    all_nodes, summary, branch_terms = run_all_cases()

    print("\n[Step 2] CSV 저장 중...")
    df_all, df_sum, df_bt = save_csvs(all_nodes, summary, branch_terms)

    print("\n[Step 3] 그래프 생성 중...")
    plot_parity(df_all)
    plot_error_bar(df_sum)
    plot_pressure_profile(df_all)
    plot_error_heatmap(df_all)

    # 결과 요약
    print("\n" + "=" * 60)
    print("검증 결과 요약")
    print("=" * 60)
    max_err = df_sum["max_node_error_pct"].max()
    mean_err = df_sum["mean_node_error_pct"].mean()
    all_same = df_sum["same_worst_branch"].all()

    print(f"  최대 노드 오차: {max_err:.4f}%")
    print(f"  평균 노드 오차: {mean_err:.4f}%")
    print(f"  최악 분기 일치: {'YES (모두 일치)' if all_same else 'NO (불일치 있음)'}")

    if max_err < 1.0:
        print(f"  판정: PASS (< 1% 허용 기준)")
    else:
        print(f"  판정: REVIEW NEEDED (> 1%)")

    print(f"\n출력 파일:")
    print(f"  CSV:   {DATA_DIR}/")
    print(f"  그래프: {FIG_DIR}/")
    print(f"  INP:   {INP_DIR}/")
    print("=" * 60)

    return max_err < 1.0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
