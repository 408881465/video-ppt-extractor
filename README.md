# video-ppt-extractor

从讲座/课程/会议录像中抽取分享者的 PPT 页面截图的 WorkBuddy 技能。

三级流水线：**ffmpeg 均匀采样 → 像素聚类筛候选 → VLM 看图墙语义把关**，
产出按时间命名的全分辨率截图。

## 为什么是两级融合

- 纯像素方法（场景检测/逐帧比对）：在"问答环节摄像头画面""分步动画"上不可靠
- 纯 VLM 方法：无法去重（同一页停留 5 分钟会产出几百张重复图）
- 本方案：廉价信号处理干粗活，语义判断做把关，兼顾准确性、成本与可解释性

## 安装

将本仓库（或 zip）放入 WorkBuddy 技能目录：

```
~/.workbuddy/skills/video-ppt-extractor/
```

依赖：`ffmpeg`（命令行）+ Python 包 `opencv-python-headless`、`numpy`（建议隔离 venv）。

## 使用

在 WorkBuddy 中对话触发，如："把这个讲座视频里的 PPT 截出来"。

手动运行脚本：

```bash
# 第 1 步：采样 + 聚类 + 生成编号图墙
python scripts/ppt_extract.py prepare 视频.mp4 --workdir WORKDIR \
    [--interval 5] [--crop 0.08,0.17,0.92,1.0]

# 第 2 步：逐张查看 WORKDIR/sheets/ 图墙，判定每个候选编号
#         保留 / 剔除(摄像头画面等) / 疑似动画中间态

# 第 3 步：按编号导出全分辨率截图
python scripts/ppt_extract.py export 视频.mp4 --workdir WORKDIR \
    --keep "1-44" --drop "2" --outdir 输出目录 --zip
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--interval` | 5s | 采样周期，按"预期最短有效停留的 1/3"取值 |
| `--crop` | 全帧 | 比对区域（画面比例），用于排除摄像头画中画等干扰 |

实测：103 分钟 720p 讲座视频，采样 13 秒、聚类 1 秒，146 张候选中
语义把关剔除 102 张摄像头画面，产出 44 张 PPT 页。
