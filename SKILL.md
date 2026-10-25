---
name: video-ppt-extractor
description: 从讲座/课程/会议录像中抽取分享者的 PPT 页面截图。三级流水线：ffmpeg 均匀采样 → 像素聚类筛候选 → VLM 看图墙语义把关，产出按时间命名的全分辨率截图。当用户要求"把视频里的 PPT/幻灯片截出来"、"从录课/讲座视频提取课件页面"、"视频抽帧做讲义"时使用本技能。适用于含 PPT 画面的录屏（含摄像头画中画干扰、问答环节等情况）。
agent_created: true
---

# Video PPT Extractor — 视频 PPT 页面抽取

## 概述

从录像中提取"分享者的 PPT 页面"截图。核心思路是**两级分工**：廉价信号处理（ffmpeg 采样 + 像素聚类）干粗活，语义判断（VLM，即执行本技能的 Agent 本身看图）做把关。纯像素方法在"问答摄像头环节""分步动画"上不可靠，纯 VLM 方法无法去重——两级融合兼顾准确性、成本与可解释性。

## 环境准备

运行脚本前确认（先 `which ffmpeg` / `ffmpeg -version` 检查，缺什么补什么）：

1. **ffmpeg**（任选其一，按平台）：
   - **Windows**：`winget install ffmpeg`，或从 ffmpeg.org 下载构建包解压后把 bin 目录加入 PATH。
   - **macOS（普通情况）**：`brew install ffmpeg`。
   - **macOS 27+（实测 brew 因 sandbox-exec 被系统封禁而失效）**：用隔离 venv 装静态二进制：
     ```bash
     <venv-python> -m pip install static-ffmpeg
     # 二进制位于 <venv-site-packages>/static_ffmpeg/bin/darwin_arm64/
     ln -sf <该目录>/ffmpeg /opt/homebrew/bin/ffmpeg
     ln -sf <该目录>/ffprobe /opt/homebrew/bin/ffprobe
     ```
2. **Python 依赖**：opencv-python-headless、numpy、Pillow（建议装入隔离 venv，勿全局安装）。

## 工作流程

### 第 0 步：确定裁剪区域（仅当画面有摄像头/干扰区域时）

用 ffmpeg 抽 1–2 帧查看（如 `ffmpeg -ss 600 -i VIDEO -frames:v 1 probe.jpg`），估算 PPT 区域在画面中的比例范围。若 PPT 占满全帧或无干扰区域，跳过 `--crop`。

### 第 1 步：采样 + 聚类 + 图墙（脚本自动完成）

```bash
<venv-python> <skill-dir>/scripts/ppt_extract.py prepare VIDEO --workdir WORKDIR \
    [--interval 5] [--crop 0.08,0.17,0.92,1.0]
```

- 产出：`WORKDIR/candidates/cand_NNN_XXhXXmXXs.jpg`（去重后的候选帧）+ `WORKDIR/sheets/sheet_N.jpg`（4×5 编号图墙，每墙 20 张候选）。
- 103 分钟视频实测约 13 秒完成。

**采样率取值依据**：周期 p 保证捕获所有停留时长 ≥ p 的页面。规则：**p ≤ 预期最短有效停留的 1/3**。讲座/录课取默认 5 秒（页面停留通常几十秒起）；快速演示录屏取 1–2 秒。

### 第 2 步：VLM 语义把关（Agent 亲自执行）

逐张 Read 图墙（每次 2–4 张），对每个候选编号判定三类：

- **保留**：完整 PPT 页（含封面、章节页、结尾致谢页）
- **剔除**：摄像头特写、讨论画面、转场、桌面等非 PPT 内容
- **疑似中间态**：淡入/淡出到一半、动画进行中的帧（特征：整体发白/叠影/文字残缺）

把关要点：
- 图墙缩略图足以判断页面类别；对疑似中间态或文字需确认的候选，单独 Read 候选原图复核。
- 若某候选判为中间态但该页值得保留：丢弃该候选，改用 `ffmpeg -ss <中间态时间+3s> -i VIDEO -frames:v 1` 手动补一张稳定帧。

### 第 3 步：导出成品

```bash
<venv-python> <skill-dir>/scripts/ppt_extract.py export VIDEO --workdir WORKDIR \
    --keep "1-44" --drop "2,17" --outdir OUTDIR [--zip] [--pdf OUT.pdf]
```

- 导出的是**全分辨率原始帧**（保留视频原始布局，不套裁剪），命名 `slide_NNN_HHhMMmSSs.jpg`，文件名按时间递增、天然有序。
- `--keep` 支持区间与枚举（`"1-44"`、`"1,3,5-9"`），`--drop` 在其基础上剔除。
- `--interval` 必须与 prepare 时一致（默认 5）。
- `--pdf` 将导出的截图**按时间顺序**拼接为多页 PDF；也可用独立子命令拼任意截图目录：
  ```bash
  <venv-python> <skill-dir>/scripts/ppt_extract.py pdf 截图目录 --out 成品.pdf [--pattern "slide_*.jpg"]
  ```
- `--zip` 打包导出目录（纯 Python 实现，Windows/macOS 均可用）。

用户要求"整理成 PDF/讲义"时，默认在导出后加 `--pdf`，并把 PDF 作为主要交付物之一。

### 第 4 步：交付

用 present_files 交付：成品 zip + 1–2 张代表性截图；向用户报告总页数、剔除内容类别（如"问答环节 102 张摄像头画面已滤除"）及疑似中间态的处理情况。

## 参数与阈值

| 参数 | 默认 | 说明 |
|---|---|---|
| `--interval` | 5s | 采样周期，按"最短有效停留的 1/3"取值 |
| `--crop` | 全帧 | 比对区域；摄像头画中画/顶部横幅必须排除，否则聚类误切分 |
| DIFF_SEG=8.0 | 脚本内 | 稳定段切分阈值（0–255 灰度均值差） |
| DIFF_DUP=4.5 | 脚本内 | 去重阈值 |

阈值适用于 720p–1080p 讲座录屏；若候选数量异常大（数倍于预期页数），多为画面存在持续小幅变动（弹幕、计时器、视频窗口），优先用 `--crop` 把它们排除在比对区外。

## 已知陷阱

- **漏检方向**：过疏采样漏"快速回翻/秒翻页"；过密采样不增加 VLM 负担（聚类兜底），只增加磁盘与解码时间——拿不准时取更密。
- **分步动画**：同一页逐条展示时，每个稳定超过采样周期的状态各成候选。这是特性（可挑最终态），交付时说明即可。
- **比对新旧结果时**：全帧图与已裁剪图的缩略图基准必须统一（全帧先裁剪再缩放，已裁剪的直接缩放），否则全是假差异。
- **批量删除**：macOS 沙箱可能拦截 `rm -rf` 大目录，清理中间产物用 `/usr/bin/trash`（macOS）；Windows 用回收站 API 或手动删除。
