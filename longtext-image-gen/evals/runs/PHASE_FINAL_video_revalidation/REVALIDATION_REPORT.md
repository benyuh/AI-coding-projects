# 视频路径端到端补验报告

执行时间：2026-04-29T18:56:35+08:00
触发条件：用户本机已 `brew install ffmpeg`
ffmpeg 版本：ffmpeg version 8.1 Copyright (c) 2000-2026 the FFmpeg developers

## 验收结果

| case_id | 字数 | 耗时 | token_in/out | mp4 | srt | 降级 | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| comp_R06 大语言模型LLM完整科普 | 5404 | 91.3s | 9220 / 未记录 | 未产出 | 未产出 | L2 | FAIL |
| comp_R10 播客逐字稿_中国人口趋势 | 5272 | 75.1s | 9761 / 未记录 | 未产出 | 未产出 | L2 | FAIL |

## 结论

[B] 视频路径未通过：两条 case 均未产出真实 `.mp4` 和 `.srt`，`summary.json` 中 `has_mp4=false`、`has_srt=false`，最终产物均为 PNG：`comp_R06/artifacts/output.png` 与 `comp_R10/artifacts/output.png`。两条均为 `degradation_level="L2"`，说明流程在 Gate 1 失败后进入 Blueprint 降级并走 Tool1 图片渲染，未进入可验收的视频产物路径。

## 附

- 完整数据：`evals/runs/PHASE_FINAL_video_revalidation/summary.json`
- 运行日志：`evals/runs/PHASE_FINAL_video_revalidation/run.log`
- 产物路径：`evals/runs/PHASE_FINAL_video_revalidation/comp_R06/artifacts/` 和 `comp_R10/artifacts/`
