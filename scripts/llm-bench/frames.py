"""Frame sampling for the benchmark: dense around t0, a few spread over the clip.

Usage: python3 frames.py <clip.mp4> <out_dir> [t0_in_clip_s=20]
Writes f_<NN>.jpg (long side <= 640 px) and frames.json [{file, clip_time_s, offset_s}].
"""
import json
import subprocess
import sys
from pathlib import Path

DENSE_OFFSETS = [round(-0.9 + 0.15 * i, 2) for i in range(13)]  # -0.9 .. +0.9 s, 6.7 fps
SPREAD_OFFSETS = [-15.0, -6.0, 6.0]  # context far from t0
MAX_SIDE = int(sys.argv[4]) if len(sys.argv) > 4 else 640


def main() -> None:
    clip, out = sys.argv[1], Path(sys.argv[2])
    t0 = float(sys.argv[3]) if len(sys.argv) > 3 else 20.0
    out.mkdir(parents=True, exist_ok=True)
    offsets = sorted(SPREAD_OFFSETS[:2] + DENSE_OFFSETS + SPREAD_OFFSETS[2:])
    meta = []
    for i, off in enumerate(offsets):
        ct = t0 + off
        f = out / f"f_{i:02d}.jpg"
        vf = f"scale='if(gt(iw,ih),min({MAX_SIDE},iw),-2)':'if(gt(iw,ih),-2,min({MAX_SIDE},ih))'"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ct:.3f}", "-i", clip, "-frames:v", "1",
                        "-vf", vf, "-q:v", "4", str(f)], check=True)
        meta.append({"file": f.name, "clip_time_s": round(ct, 3), "offset_s": off})
    (out / "frames.json").write_text(json.dumps(meta, indent=1))
    print(f"{len(meta)} frames -> {out}")


if __name__ == "__main__":
    main()
