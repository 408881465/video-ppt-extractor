#!/usr/bin/env python3
"""视频 PPT 页面抽取流水线（确定性部分）。

两个子命令:
  prepare  ffmpeg 均匀采样 + 像素聚类 → 候选帧 + 编号图墙(供 VLM/人工把关)
  export   把关后按候选编号导出全分辨率截图

依赖: ffmpeg(命令行)、opencv-python-headless、numpy
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

import cv2
import numpy as np

DIFF_SEG = 8.0    # 相邻采样帧差异超过此值 → 稳定段切分
DIFF_DUP = 4.5    # 候选与已保留页差异小于此值 → 去重
THUMB = (160, 90)
SHEET_COLS, SHEET_ROWS = 4, 5


def hms(t: float) -> str:
    return f"{int(t // 3600):02d}h{int(t % 3600 // 60):02d}m{int(t % 60):02d}s"


def parse_hms(s: str) -> int:
    m = re.search(r"(\d+)h(\d+)m(\d+)s", s)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def parse_crop(s):
    """'0.08,0.17,0.92,1.0' -> (x1,y1,x2,y2) 比例; None 表示不裁剪"""
    if not s:
        return None
    x1, y1, x2, y2 = (float(v) for v in s.split(","))
    return (x1, y1, x2, y2)


def crop_img(img, crop):
    if crop is None:
        return img
    h, w = img.shape[:2]
    x1, y1, x2, y2 = crop
    return img[int(h * y1):int(h * y2), int(w * x1):int(w * x2)]


def thumb(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, THUMB, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(g, (5, 5), 0)


def diff(a, b):
    return float(np.mean(cv2.absdiff(a, b)))


def run_prepare(args):
    workdir = os.path.abspath(args.workdir)
    fdir = os.path.join(workdir, "frames")
    cdir = os.path.join(workdir, "candidates")
    sdir = os.path.join(workdir, "sheets")
    for d in (fdir, cdir, sdir):
        os.makedirs(d, exist_ok=True)

    # 1) ffmpeg 均匀采样
    print(f"[1/3] ffmpeg 采样: 每 {args.interval}s 一帧 ...", flush=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", args.video,
         "-vf", f"fps=1/{args.interval}", "-q:v", "3",
         os.path.join(fdir, "f_%04d.jpg")],
        check=True)
    files = sorted(glob.glob(os.path.join(fdir, "f_*.jpg")))
    if not files:
        sys.exit("采样失败: 未产出任何帧,请检查视频路径与 ffmpeg")
    print(f"      共 {len(files)} 帧", flush=True)

    # 2) 像素聚类: 稳定段切分 + 代表帧 + 去重
    print("[2/3] 聚类筛选候选 ...", flush=True)
    crop = parse_crop(args.crop)
    kept, prev = [], None
    cands = []
    for i, f in enumerate(files):
        g = thumb(crop_img(cv2.imread(f), crop))
        if prev is None:
            prev, seg_start = g, i
            continue
        if diff(g, prev) > DIFF_SEG:
            cands.append(i - 1)
            prev, seg_start = g, i
        else:
            prev = g
    cands.append(len(files) - 1)

    for idx in cands:
        img = cv2.imread(files[idx])
        g = thumb(crop_img(img, crop))
        if any(diff(g, k[1]) < DIFF_DUP for k in kept):
            continue
        kept.append((idx, g))
        out = os.path.join(cdir, f"cand_{len(kept):03d}_{hms(idx * args.interval)}.jpg")
        cv2.imwrite(out, crop_img(img, crop), [cv2.IMWRITE_JPEG_QUALITY, 92])

    # 3) 编号图墙(供 VLM/人工把关)
    print("[3/3] 生成图墙 ...", flush=True)
    cand_files = sorted(glob.glob(os.path.join(cdir, "cand_*.jpg")))
    TW, TH = 320, 178
    per = SHEET_COLS * SHEET_ROWS
    for s in range((len(cand_files) + per - 1) // per):
        batch = cand_files[s * per:(s + 1) * per]
        sheet = np.full((SHEET_ROWS * (TH + 28) + 10, SHEET_COLS * (TW + 10) + 10, 3), 245, np.uint8)
        for i, f in enumerate(batch):
            th = cv2.resize(cv2.imread(f), (TW, TH))
            r, c = divmod(i, SHEET_COLS)
            y, x = 10 + r * (TH + 28), 10 + c * (TW + 10)
            sheet[y:y + TH, x:x + TW] = th
            cv2.putText(sheet, os.path.basename(f).replace("cand_", "").replace(".jpg", ""),
                        (x, y + TH + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1, cv2.LINE_AA)
        cv2.imwrite(os.path.join(sdir, f"sheet_{s + 1}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])

    print(f"完成: {len(cand_files)} 张候选 -> {cdir}")
    print(f"图墙 -> {sdir}")
    print("下一步: 逐张查看图墙,记录『保留/剔除/疑似动画中间态』的候选编号,再运行 export 子命令。")


def build_pdf(image_files, pdf_path):
    """按传入顺序将图片拼接为多页 PDF(需 Pillow)。"""
    try:
        from PIL import Image
    except ImportError:
        sys.exit("需要 Pillow: <venv-python> -m pip install Pillow")
    pages = []
    for f in image_files:
        img = Image.open(f)
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        pages.append(img)
    if not pages:
        sys.exit("没有可写入 PDF 的图片")
    pages[0].save(pdf_path, save_all=True, append_images=pages[1:], resolution=96.0)
    print(f"已生成 PDF({len(pages)} 页) -> {pdf_path}")


def zip_dir(src_dir, zp):
    import zipfile
    base = os.path.basename(src_dir.rstrip("/\\"))
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src_dir):
            for f in sorted(files):
                full = os.path.join(root, f)
                z.write(full, os.path.join(base, os.path.relpath(full, src_dir)))
    print(f"已打包 -> {zp}")


def run_export(args):
    workdir = os.path.abspath(args.workdir)
    cdir = os.path.join(workdir, "frames")
    cands = sorted(glob.glob(os.path.join(workdir, "candidates", "cand_*.jpg")))
    if not cands:
        sys.exit("未找到候选帧,请先运行 prepare")

    keep = set()
    for part in args.keep.split(","):
        if "-" in part:
            a, b = part.split("-")
            keep.update(range(int(a), int(b) + 1))
        else:
            keep.add(int(part))
    for part in (args.drop or "").split(","):
        if part.strip():
            keep.discard(int(part))

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    exported = []
    n = 0
    for f in cands:
        num = int(os.path.basename(f).split("_")[1])
        if num not in keep:
            continue
        t = parse_hms(os.path.basename(f))
        fid = t // args.interval + 1
        src = os.path.join(cdir, f"f_{fid:04d}.jpg")
        if not os.path.exists(src):
            print(f"  跳过 cand_{num:03d}: 找不到 {src}")
            continue
        # 导出全分辨率原始帧(含原始布局),不套裁剪;文件名按时间递增,天然有序
        dst = os.path.join(outdir, f"slide_{n + 1:03d}_{hms(t)}.jpg")
        shutil.copy(src, dst)
        exported.append(dst)
        n += 1
    print(f"已导出 {n} 张 -> {outdir}")
    if args.pdf:
        build_pdf(exported, os.path.abspath(args.pdf))
    if args.zip:
        base = os.path.basename(outdir.rstrip("/\\"))
        zip_dir(outdir, os.path.join(os.path.dirname(outdir), base + ".zip"))


def run_pdf(args):
    # 独立子命令:把任意目录下的截图(按文件名排序=时间序)拼成 PDF
    files = sorted(glob.glob(os.path.join(os.path.abspath(args.imgdir), args.pattern)))
    if not files:
        sys.exit(f"未找到图片: {args.imgdir}/{args.pattern}")
    build_pdf(files, os.path.abspath(args.out))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("prepare", help="采样+聚类+图墙")
    pp.add_argument("video")
    pp.add_argument("--workdir", required=True)
    pp.add_argument("--interval", type=int, default=5, help="采样周期(秒),默认 5")
    pp.add_argument("--crop", default=None,
                    help="比对区域 x1,y1,x2,y2 (画面比例),用于排除摄像头等干扰区域;不传则全帧比对")

    pe = sub.add_parser("export", help="按候选编号导出全分辨率截图")
    pe.add_argument("video")
    pe.add_argument("--workdir", required=True)
    pe.add_argument("--keep", required=True, help='保留的候选编号,如 "1-44" 或 "1,3,5-9"')
    pe.add_argument("--drop", default="", help="在 keep 基础上剔除,如 \"2,17\"")
    pe.add_argument("--outdir", required=True)
    pe.add_argument("--interval", type=int, default=5, help="须与 prepare 一致")
    pe.add_argument("--zip", action="store_true", help="导出后打包 zip")
    pe.add_argument("--pdf", default=None, help="同时拼接为 PDF 文件,值为其路径")

    pf = sub.add_parser("pdf", help="把截图目录拼成 PDF(按文件名排序)")
    pf.add_argument("imgdir")
    pf.add_argument("--out", required=True, help="PDF 输出路径")
    pf.add_argument("--pattern", default="slide_*.jpg", help="文件匹配模式,默认 slide_*.jpg")

    args = p.parse_args()
    {"prepare": run_prepare, "export": run_export, "pdf": run_pdf}[args.cmd](args)


if __name__ == "__main__":
    main()
