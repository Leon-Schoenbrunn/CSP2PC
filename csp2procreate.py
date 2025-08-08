#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import shutil
import sqlite3
import struct
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import List
from plistlib import UID
import plistlib
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageChops, ImageOps, ImageFilter

# ---------------------------------------------------------------------------
# Helper constants -----------------------------------------------------------
# ---------------------------------------------------------------------------

IEND_MARKER = b"IEND"
SQLITE_MAGIC = b"SQLite format 3"  

# ---------------------------------------------------------------------------
# Stage 1 - PNG extraction helpers -------------------------------------------
# ---------------------------------------------------------------------------

def prepare_shape(src: Path, dest: Path) -> None:
    """
    Convert CSP stamp → Procreate tip
    """
    im = Image.open(src).convert("RGBA")
    r, g, b, a = im.split()
    gray = Image.merge("RGB", (r, g, b)).convert("L")
    inv = ImageOps.invert(gray)
    mask = ImageChops.multiply(inv, a)

    mask.save(dest, format="PNG")   # L-mode PNG

def locate_sqlite_offset(data: bytes) -> int:
    off = data.find(SQLITE_MAGIC)
    if off == -1:
        raise ValueError("SQLite header not found — is this a .sut file?")
    return off

def dump_sqlite_blob(sut_path: Path) -> Path:
    raw = sut_path.read_bytes()
    off = locate_sqlite_offset(raw)
    tmp = Path(tempfile.mktemp(suffix=".sqlite"))
    tmp.write_bytes(raw[off:])
    return tmp

def _extract_png_from_layer(blob: bytes) -> bytes:
    pos_png = blob.rfind(b"PNG")
    if pos_png <= 0:
        raise RuntimeError("PNG signature not found in layer blob")
    begin = pos_png - 1
    pos_iend = blob.rfind(IEND_MARKER)
    if pos_iend == -1:
        raise RuntimeError("IEND marker not found in layer blob")
    end = pos_iend + 4
    return blob[begin:end]

def get_seed_path() -> Path:
    if getattr(sys, 'frozen', False):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).parent
    return base / "Seed.brush"

def write_quicklook_thumbnail(bundle_dir: Path, original_stamp_png: Path) -> None:
    ql_dir = bundle_dir / "QuickLook"
    ql_dir.mkdir(exist_ok=True)
    thumb_w, thumb_h = 1060, 324
    bg = Image.new("RGBA", (thumb_w, thumb_h), (0, 0, 0, 0))  # transparent
    stamp = Image.open(original_stamp_png).convert("RGBA")
    scale = thumb_h / stamp.height
    new_w = int(stamp.width * scale)
    new_h = int(stamp.height * scale)
    stamp = stamp.resize((new_w, new_h), Image.LANCZOS)
    x_pos = (thumb_w - new_w) // 2
    y_pos = (thumb_h - new_h) // 2
    bg.alpha_composite(stamp, (x_pos, y_pos))
    (ql_dir / "Thumbnail.png").write_bytes(b"")  # clear old file if exists
    bg.save(ql_dir / "Thumbnail.png", "PNG")
    
def pick_files():
    root = tk.Tk()
    root.withdraw()
    sut_path = filedialog.askopenfilename(
        title="Select CSP Brush (.sut)",
        filetypes=[("CSP Brush", "*.sut")]
    )
    if not sut_path:
        sys.exit("No .sut selected.")

    dest_dir = filedialog.askdirectory(title="Select Output Folder")
    if not dest_dir:
        sys.exit("No output folder selected.")
    return Path(sut_path), Path(dest_dir)

def extract_pngs_from_sut(sut_path: Path, dest_dir: Path) -> List[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    sqlite_tmp = dump_sqlite_blob(sut_path)
    con = sqlite3.connect(sqlite_tmp)
    cur = con.cursor()
    rows = cur.execute("SELECT _PW_ID, FileData FROM MaterialFile").fetchall()
    if not rows:
        raise RuntimeError("No FileData rows found — brush has no embedded images?")

    paths: List[Path] = []
    for idx, (_id, blob) in enumerate(rows):
        try:
            png_bytes = _extract_png_from_layer(blob)
            path = dest_dir / f"stamp_{idx:02d}.png"
            path.write_bytes(png_bytes)
            paths.append(path)
        except Exception as exc:
            print(f"[WARN] layer {_id} skipped: {exc}")
    cur.close(); con.close(); sqlite_tmp.unlink(missing_ok=True)
    return paths

# ---------------------------------------------------------------------------
# Stage 2 - Create Brush -----------------------------------------------------
# ---------------------------------------------------------------------------

def read_variant_row(sqlite_path: Path):
    """
    Helper function to read the variant row from the .sut (sql) database
    """
    con = sqlite3.connect(sqlite_path)
    row = con.execute("SELECT * FROM Variant LIMIT 1").fetchone()
    names = [d[1] for d in con.execute("PRAGMA table_info('Variant')")]
    con.close()
    return dict(zip(names, row))
    
def csp_to_plotSpacing(variant):
    size_px     = variant.get("BrushSize", 1) or 1
    interval_px = variant.get("BrushInterval", 0) or 0

    if size_px <= 0:
        return 0.01
    raw_spacing = interval_px / size_px

    # Adjust fudge factor: larger brushes can have higher spacing multipliers
    if size_px < 50:
        fudge_factor = 0.15
    elif size_px < 100:
        fudge_factor = 0.3
    else:
        fudge_factor = 0.6

    adjusted_spacing = raw_spacing * fudge_factor
    return max(0.01, min(1.0, adjusted_spacing))


def map_csp_rendering_flags(variant):
    """
    Helper function to somewhat map to procreates rendering settings
    """
    use_watercolor = bool(variant.get("BrushUseWaterColor", 0))
    mix_color      = float(variant.get("BrushMixColor", 0.0) or 0)
    mix_alpha      = float(variant.get("BrushMixAlpha", 0.0) or 0)
    brush_flow     = float(variant.get("BrushFlow", 1.0) or 1.0)

    # -- Light Glaze --
    flags = {
        "renderingMaxTransfer": True,
        "renderingModulatedTransfer": False,
        "renderingRecursiveMixing": False,
    }

    if use_watercolor or mix_color > 0.05 or mix_alpha > 0.05:
        flags["renderingRecursiveMixing"] = True

        if mix_color >= 0.75 or mix_alpha >= 0.75:
            # Intense Glaze
            flags["renderingMaxTransfer"] = True
            flags["renderingModulatedTransfer"] = True
        elif brush_flow > 0.8:
            # Heavy Glaze
            flags["renderingMaxTransfer"] = True
            flags["renderingModulatedTransfer"] = False
        else:
            # Uniform Blending
            flags["renderingMaxTransfer"] = False
            flags["renderingModulatedTransfer"] = True

    return flags

def map_csp_to_wet_mix(variant):
    """
    Helper function to somewhat map to procreates wet-mix settings
    """
    use_wc   = bool(variant.get("BrushUseWaterColor", 0))
    mix_col  = float(variant.get("BrushMixColor", 0) or 0)
    mix_alpha= float(variant.get("BrushMixAlpha", 0) or 0)
    flow     = float(variant.get("BrushFlow", 100) or 100)
    nc = max(0.0, min(1.0, mix_col/100.0))
    na = max(0.0, min(1.0, mix_alpha/100.0))
    nf = max(0.0, min(1.0, flow/100.0))
    if not use_wc and nc < 0.5:
        dilution = 0.0
    else:
        gamma = 0.75
        cap   = 0.7
        base = nc ** gamma
        flow_brake = 0.5 + 0.5*(1.0 - nf)
        alpha_push = 0.2*na

        dilution = min(cap, base*cap*flow_brake + alpha_push*cap)

    charge = max(0.2, 1.0 - dilution)
    attack = 1.0 - 0.5*na
    pull = 0.6 if use_wc else 0.0

    return {
        "wetMixDilution": dilution,
        "wetMixCharge":   charge,
        "wetMixAttack":   attack,
        "wetMixPull":     pull,
    }


def finalise_seed_brush(bundle_dir: Path, stamp_png: Path, new_name: str, sql_tmp: Path | None = None) -> None:
    """
    bundle_dir contains Brush.archive and the freshly-copied Shape.png
    stamp_png  : path to the stamp (already grayscale or not)
    new_name   : visible name in Brush Library
    sql_tmp:   : path to the .sut file to access all it's properties
    """
    Image.open(stamp_png).convert("L").save(bundle_dir / "Shape.png")

    plist_path = bundle_dir / "Brush.archive"
    root       = plistlib.load(plist_path.open("rb"))
    objs       = root["$objects"]

    def resolve(val):
        """follow UID indirection until we reach the real object"""
        while isinstance(val, UID):
            val = objs[val.data]
        return val

    # 2 ─ bump creation timestamp & rename brush
    stamped, renamed = False, False
    now = float(time.time())
    variant = read_variant_row(sql_tmp)
    spacing = csp_to_plotSpacing(variant)
    min_px, max_px, opacity = 0.02, 3.0, 1.0
    flow_val = variant.get("BrushFlow",  1000)
    flow_val = max(0, min(1000, flow_val))
    randomized  = variant.get("BrushRotationRandomInSpray")
    BrushUseSpray  = variant.get("BrushUseSpray")
    BrushRotationInSpray  = variant.get("BrushRotationInSpray")
    if not (randomized == 0) and BrushUseSpray and BrushRotationInSpray:
        randomized = True
    else:
        randomized = False
    BrushRotation = variant.get("BrushRotation")
    BrushPatternOrderType = variant.get("BrushPatternOrderType", 0)
    BrushRotationRandomScale = variant.get("BrushRotationRandomScale", 0)/100
    interval_px = variant.get("BrushInterval")
    BrushSprayBias  = variant.get("BrushSprayBias")
    jitter_val  = (variant.get("BrushRevision")/100)*2
    BrushUseIn  = variant.get("BrushUseIn")
    BrushUseOut  = variant.get("BrushUseOut")
    BrushInLength  = variant.get("BrushInLength")
    BrushOutLength  = variant.get("BrushOutLength")
    BrushInRatio  = variant.get("BrushInRatio")
    BrushOutRatio  = variant.get("BrushOutRatio")
    render_flags = map_csp_rendering_flags(variant)
    wetMix_flags = map_csp_to_wet_mix(variant)
    angle_sensitive = variant.get("BrushRotationEffector") == 3

    for obj in objs:
        if not isinstance(obj, dict):
            continue

        # (a) creationDate → NS.time
        if not stamped and ("creationDate" in obj or b"creationDate" in obj):
            cd = resolve(obj.get("creationDate") or obj.get(b"creationDate"))
            if isinstance(cd, dict) and ("NS.time" in cd or b"NS.time" in cd):
                key = "NS.time" if "NS.time" in cd else b"NS.time"
                cd[key] = now
                stamped = True

        # (b) visible name
        if not renamed and ("name" in obj or b"name" in obj):
            objs[obj.get("name")] = new_name
            renamed  = True
            
        # (c) spacing
        if "plotSpacing" in obj:
            obj["plotSpacing"] = float(spacing)
            
        # (d) minSize
        if "minSize" in obj:
            obj["minSize"] = float(min_px)
            
        # (e) maxSize
        if "maxSize" in obj:
            obj["maxSize"] = float(max_px)
            
        # (f) opacity
        if "maxOpacity" in obj:
            obj["maxOpacity"] = float(opacity)
            
        # (g) rotation
        if "shapeRotation" in obj:
            obj["shapeRotation"] = float(BrushRotation)
            
        # (h) randomized
        if "shapeRandomise" in obj:
            obj["shapeRandomise"] = randomized  
            
        # (i) scatter
        if "shapeRandomise" in obj:
            if BrushPatternOrderType == 3:
                obj["shapeScatter"] = float(BrushRotationRandomScale)
            else:
                obj["shapeScatter"] = float(0)
                
        # (j) FlipX
        if "shapeFlipXJitter" in obj and BrushPatternOrderType == 3:
            obj["shapeFlipXJitter"] = True
        elif "shapeFlipXJitter" in obj and not BrushPatternOrderType == 3:
            obj["shapeFlipXJitter"] = False
            
        # (k) jitter
        if "plotJitter" in obj:
            obj["plotJitter"] = float(jitter_val) 
            
        # (l) Taper
        if "taperPressure" in obj:
            obj["taperPressure"] = float(0)
        if BrushUseIn or BrushUseOut:
            if "pencilTaperStartLength" in obj:
                obj["pencilTaperStartLength"] = float((BrushInLength/100)/4)
            if "pencilTaperEndLength" in obj:
                obj["pencilTaperEndLength"] = float((BrushOutLength/100)/2)
                
        # (m) render mode
        if "renderingMaxTransfer" in obj:
            obj["renderingMaxTransfer"] = render_flags['renderingMaxTransfer']
        if "renderingModulatedTransfer" in obj:
            obj["renderingModulatedTransfer"] = render_flags['renderingModulatedTransfer']
        if "renderingRecursiveMixing" in obj:
            obj["renderingRecursiveMixing"] = render_flags['renderingRecursiveMixing']
            
        # (n) wet-mix
        if "dynamicsMix" in obj:
            obj["dynamicsMix"] = float(wetMix_flags['wetMixDilution'])
        if "dynamicsLoad" in obj:
            obj["dynamicsLoad"] = float(wetMix_flags['wetMixCharge'])
        if "dynamicsPressureMix" in obj:
            obj["dynamicsPressureMix"] = float(wetMix_flags['wetMixAttack'])
        if "dynamicsWetAccumulation" in obj:
            obj["dynamicsWetAccumulation"] = float(wetMix_flags['wetMixPull'])
            
        # (o) shape input-style for angle sensitivity
        if angle_sensitive:
            if "shapeAzimuth" in obj:
                obj["shapeAzimuth"] = True
            if "shapeRoll" in obj:
                obj["shapeRoll"] = True
            if "shapeRollMode" in obj:
                obj["shapeRollMode"] = 1
            if "shapeOrientation" in obj:
                obj["shapeOrientation"] = 1

        if stamped and renamed:
            break

    plistlib.dump(root, plist_path.open("wb"), fmt=plistlib.FMT_BINARY)

def build_brush(
    shape_png: Path,
    brush_out: Path,
    seed_brush: Path,
    display_name: str | None = None,
    sql_tmp: Path | None = None,
) -> None:
    """
    Create a Procreate **.brush** by copying *seed_brush* and replacing its
    Shape.png with *shape_png*.

    Parameters
    ----------
    shape_png     The stamp you want to use (must be grayscale PNG).
    brush_out     Where to write the finished .brush file.
    seed_brush    A previously-exported brush that acts as a template.
    display_name  Optional filename shown inside Procreate’s library; if None
                  we derive it from `brush_out.stem`.
    sql_tmp       Path of the .sut file to access further brush properties.
    """
    tmp_dir = brush_out.parent / f".tmp_{uuid.uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 1. Unzip seed into temp dir
    with zipfile.ZipFile(seed_brush) as z:
        z.extractall(tmp_dir)

    # 3. Update Name, prepare the brush stamp and edit its properties
    if display_name:
        (tmp_dir / "Title.txt").write_text(display_name)
    tmp_shape = tmp_dir / "Shape.png"
    write_quicklook_thumbnail(tmp_dir, shape_png)
    prepare_shape(shape_png, tmp_shape) 
    finalise_seed_brush(tmp_dir, tmp_shape, display_name, sql_tmp)

    # 4. Re-zip → .brush
    with zipfile.ZipFile(brush_out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for item in tmp_dir.rglob("*"):
            z.write(item, arcname=item.relative_to(tmp_dir))

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(
        f"✓ built .brush ({shape_png.name}) → {brush_out.name}  "
        f"[{_dt.datetime.now().strftime('%H:%M:%S')}]"
    )

def build_brushset(brush_files: List[Path], set_out: Path, set_name: str) -> None:
    """
    • brush_files : list of finished .brush files (zip archives)
    • set_out     : final .brushset path
    • set_name    : name shown in Procreate’s Brush Library
    """
    tmp_set = Path(tempfile.mkdtemp())
    uuids   = []
    for bf in brush_files:
        uid = str(uuid.uuid4()).upper()
        uuids.append(uid)
        dest_dir = tmp_set / uid
        dest_dir.mkdir()

        # unzip .brush into its UUID folder
        with zipfile.ZipFile(bf) as zf:
            zf.extractall(dest_dir)
    plist_path = tmp_set / "brushset.plist"
    plist_path.write_bytes(
        plistlib.dumps({"name": set_name, "brushes": uuids}, fmt=plistlib.FMT_XML)
    )
    with zipfile.ZipFile(set_out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in tmp_set.rglob("*"):
            z.write(item, arcname=item.relative_to(tmp_set))

    shutil.rmtree(tmp_set, ignore_errors=True)
    print(f"✓ built brush-set → {set_out.name}")

def main(argv: List[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="CSP → Procreate converter")
    ap.add_argument("sut",  help="Input .sut file")
    ap.add_argument("dest", help="Output directory")

    if argv is None and len(sys.argv) == 1:
        sut, dest = pick_files()
    else:
        args = ap.parse_args(argv)
        sut, dest = Path(args.sut), Path(args.dest)

    seed = get_seed_path()
    sut  = Path(sut).resolve()
    out  = Path(dest).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # 1) extract stamps + keep sqlite tmp for parameter mapping
    png_dir = out / sut.stem
    png_dir.mkdir(parents=True, exist_ok=True)
    pngs = extract_pngs_from_sut(sut, png_dir)
    if not pngs:
        raise SystemExit("No PNG stamps found in the .sut")

    sqlite_tmp = dump_sqlite_blob(sut)

    built_paths: list[Path] = []
    try:
        # 2) build a brush per stamp
        for i, stamp in enumerate(pngs, start=1):
            if len(pngs) == 1:
                brush_path = out / f"{sut.stem}.brush"
                display_name = sut.stem
            else:
                brush_path = out / f"{sut.stem}_{i}.brush"
                display_name = f"{sut.stem} {i}"
            build_brush(
                shape_png=stamp,
                brush_out=brush_path,
                seed_brush=seed,
                display_name=f"{sut.stem} {i}",
                sql_tmp=sqlite_tmp,
            )
            built_paths.append(brush_path)

        # 3) bundle as .brushset when there’s more than one
        if len(built_paths) > 1:
            set_path = out / f"{sut.stem}.brushset"
            build_brushset(built_paths, set_path, sut.stem)
            print(f"✓ bundled {len(built_paths)} brushes into {set_path.name}")
        else:
            print(f"✓ generated single brush → {built_paths[0].name}")
    finally:
        # 4) cleanup
        sqlite_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
