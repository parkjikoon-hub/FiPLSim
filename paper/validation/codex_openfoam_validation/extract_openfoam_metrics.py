import csv
import json
import math
import re
from pathlib import Path


ROOT = Path("/root/openfoam_validation")
CASES = ["baseline", "bead1p5", "bead3p0"]
TIME_DIR = "1.5"
PATCHES = ["inlet", "outlet1", "outlet2"]


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    return text


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_boundary(boundary_path: Path):
    text = strip_comments(read_text(boundary_path))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    patches = {}
    for i, line in enumerate(lines):
        if line in PATCHES and i + 1 < len(lines) and lines[i + 1] == "{":
            name = line
            block_lines = []
            depth = 0
            j = i + 1
            while j < len(lines):
                block_lines.append(lines[j])
                depth += lines[j].count("{") - lines[j].count("}")
                if depth == 0:
                    break
                j += 1
            block = "\n".join(block_lines)
            nfaces = int(re.search(r"nFaces\s+(\d+);", block).group(1))
            start = int(re.search(r"startFace\s+(\d+);", block).group(1))
            patches[name] = {"nFaces": nfaces, "startFace": start}
    return patches


def parse_points(points_path: Path):
    text = strip_comments(read_text(points_path))
    start = text.index("(")
    count = int(text[:start].strip().split()[-1])
    body = text[start:]
    pts = re.findall(r"\(\s*([^\(\)]+?)\s*\)", body)
    points = []
    for item in pts[:count]:
        x, y, z = [float(v) for v in item.split()]
        points.append((x, y, z))
    return points


def parse_faces(faces_path: Path):
    text = strip_comments(read_text(faces_path))
    start = text.index("(")
    count = int(text[:start].strip().split()[-1])
    body = text[start:]
    face_items = re.findall(r"(\d+)\s*\(([^)]*)\)", body)
    faces = []
    for n_str, item in face_items[:count]:
        idxs = [int(v) for v in item.split()]
        faces.append(idxs)
    return faces


def parse_owner(owner_path: Path):
    text = strip_comments(read_text(owner_path))
    start = text.index("(")
    count = int(text[:start].strip().split()[-1])
    body = text[start:]
    vals = [int(v) for v in re.findall(r"-?\d+", body)]
    return vals[:count]


def parse_internal_field(field_path: Path, vector: bool):
    text = strip_comments(read_text(field_path))
    if vector:
        m = re.search(r"internalField\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\)\s*;", text, flags=re.S)
        if not m:
            raise ValueError(f"Could not parse vector internalField from {field_path}")
        count = int(m.group(1))
        vals = [
            tuple(float(v) for v in item.split())
            for item in re.findall(r"\(\s*([^\(\)]+?)\s*\)", m.group(2))
        ]
        return vals[:count]
    m = re.search(r"internalField\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;", text, flags=re.S)
    if not m:
        raise ValueError(f"Could not parse scalar internalField from {field_path}")
    count = int(m.group(1))
    vals = [float(v) for v in m.group(2).split()]
    return vals[:count]


def face_area_vector(face_indices, points):
    verts = [points[i] for i in face_indices]
    nx = ny = nz = 0.0
    for i, p0 in enumerate(verts):
        p1 = verts[(i + 1) % len(verts)]
        nx += (p0[1] - p1[1]) * (p0[2] + p1[2])
        ny += (p0[2] - p1[2]) * (p0[0] + p1[0])
        nz += (p0[0] - p1[0]) * (p0[1] + p1[1])
    return (0.5 * nx, 0.5 * ny, 0.5 * nz)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm(v):
    return math.sqrt(dot(v, v))


def compute_case(case_dir: Path):
    boundary = parse_boundary(case_dir / "constant/polyMesh/boundary")
    points = parse_points(case_dir / "constant/polyMesh/points")
    faces = parse_faces(case_dir / "constant/polyMesh/faces")
    owner = parse_owner(case_dir / "constant/polyMesh/owner")
    p_path = case_dir / TIME_DIR / "p"
    u_path = case_dir / TIME_DIR / "U"
    p_internal = parse_internal_field(p_path, vector=False)
    u_internal = parse_internal_field(u_path, vector=True)

    result = {"case": case_dir.name, "time": TIME_DIR, "patches": {}}
    for patch_name in PATCHES:
        patch = boundary[patch_name]
        start = patch["startFace"]
        end = start + patch["nFaces"]
        patch_faces = faces[start:end]
        area_vecs = [face_area_vector(face, points) for face in patch_faces]
        areas = [norm(v) for v in area_vecs]
        total_area = sum(areas)
        owner_cells = owner[start:end]
        p_vals = [p_internal[idx] for idx in owner_cells]
        u_vals = [u_internal[idx] for idx in owner_cells]

        p_avg = sum(p * a for p, a in zip(p_vals, areas)) / total_area
        flow = sum(dot(u, av) for u, av in zip(u_vals, area_vecs))
        un_avg = flow / total_area
        speed_avg = sum(norm(u) * a for u, a in zip(u_vals, areas)) / total_area

        result["patches"][patch_name] = {
            "nFaces": patch["nFaces"],
            "area_m2": total_area,
            "p_area_avg_Pa": p_avg,
            "flow_m3_s": flow,
            "normal_velocity_avg_m_s": un_avg,
            "speed_area_avg_m_s": speed_avg,
        }

    inlet_p = result["patches"]["inlet"]["p_area_avg_Pa"]
    out1_p = result["patches"]["outlet1"]["p_area_avg_Pa"]
    out2_p = result["patches"]["outlet2"]["p_area_avg_Pa"]
    result["derived"] = {
        "dp_inlet_to_outlet1_Pa": inlet_p - out1_p,
        "dp_inlet_to_outlet2_Pa": inlet_p - out2_p,
        "total_outflow_m3_s": abs(result["patches"]["outlet1"]["flow_m3_s"])
        + abs(result["patches"]["outlet2"]["flow_m3_s"]),
        "mass_balance_error_m3_s": result["patches"]["inlet"]["flow_m3_s"]
        + result["patches"]["outlet1"]["flow_m3_s"]
        + result["patches"]["outlet2"]["flow_m3_s"],
    }
    return result


def main():
    results = [compute_case(ROOT / case) for case in CASES]

    out_dir = Path("/mnt/c/Users/INTEL/Documents/Playground/_analysis_sim/openfoam_validation")
    json_path = out_dir / "openfoam_patch_metrics.json"
    csv_path = out_dir / "openfoam_patch_metrics.csv"

    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    rows = []
    for res in results:
        row = {"case": res["case"], "time": res["time"]}
        for patch_name, metrics in res["patches"].items():
            prefix = patch_name
            for key, value in metrics.items():
                row[f"{prefix}_{key}"] = value
        row.update(res["derived"])
        rows.append(row)

    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(results, indent=2))
    print(f"\nWrote: {json_path}")
    print(f"Wrote: {csv_path}")


if __name__ == "__main__":
    main()
