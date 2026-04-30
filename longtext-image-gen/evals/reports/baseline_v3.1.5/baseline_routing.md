# v3.1.5 Baseline — 平台路由一致率

- 数据源：`/Users/benyuhang/Desktop/longtext_project/longtext_v3.1 _fix/evals/runs` latest-per-case-id，case_regex=`^(comp_R\d+|func_)`
- 有 target_platform 的案例数：**16**
- 严格一致率：**3/16 (18.8%)**
- 兼容一致率（wechat_official 归一到 wechat_moments）：**7/16 (43.8%)**

## 1. 平台分布

| type | platform | count |
| --- | --- | ---: |
| target | xiaohongshu | 11 |
| target | wechat_official | 4 |
| target | wechat_moments | 1 |
| actual | wechat_moments | 14 |
| actual | xiaohongshu | 2 |

## 2. 偏置方向（按兼容口径统计）

| target -> actual | count |
| --- | ---: |
| xiaohongshu->wechat_moments | 9 |

## 3. mismatch 分类

- **comprehensive**: 3 (comp_R01, comp_R04, comp_R05)
- **functional**: 6 (func_01_policy_general, func_02_policy_pro, func_03_research_pro, func_06_video_timeline, func_07_multi_source, func_08_video_research)

## 4. 全部案例明细

| case_id | run | category | target | normalized | actual | exact | compatible | article_type | style |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| comp_R01 | 20260429_1527_comprehensive | comprehensive | xiaohongshu | xiaohongshu | wechat_moments | False | False | policy | clean_business |
| comp_R02 | 20260429_1527_comprehensive | comprehensive | wechat_official | wechat_moments | wechat_moments | False | True | research | data_journalism |
| comp_R03 | 20260429_1527_comprehensive | comprehensive | wechat_official | wechat_moments | wechat_moments | False | True | analysis | data_journalism |
| comp_R04 | 20260429_1527_comprehensive | comprehensive | xiaohongshu | xiaohongshu | wechat_moments | False | False | tutorial | clean_business |
| comp_R05 | 20260429_1527_comprehensive | comprehensive | xiaohongshu | xiaohongshu | wechat_moments | False | False | analysis | clean_business |
| comp_R07 | 20260429_1527_comprehensive | comprehensive | wechat_official | wechat_moments | wechat_moments | False | True | analysis | tech_minimal |
| comp_R08 | 20260429_1527_comprehensive | comprehensive | wechat_official | wechat_moments | wechat_moments | False | True | analysis | data_journalism |
| comp_R09 | 20260429_1527_comprehensive | comprehensive | xiaohongshu | xiaohongshu | xiaohongshu | True | True | tutorial | xiaohongshu_warm |
| func_01_policy_general | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | wechat_moments | False | False | policy | clean_business |
| func_02_policy_pro | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | wechat_moments | False | False | policy | clean_business |
| func_03_research_pro | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | wechat_moments | False | False | research | data_journalism |
| func_04_news_event_general | 20260429_1610_functional | functional | wechat_moments | wechat_moments | wechat_moments | True | True | news | data_journalism |
| func_05_personal_opinion_general | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | xiaohongshu | True | True | opinion | xiaohongshu_warm |
| func_06_video_timeline | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | wechat_moments | False | False | analysis | data_journalism |
| func_07_multi_source | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | wechat_moments | False | False | research | data_journalism |
| func_08_video_research | 20260429_1610_functional | functional | xiaohongshu | xiaohongshu | wechat_moments | False | False | analysis | data_journalism |

## 5. v3.1.6 验收对照基线

- baseline strict match = **18.8%** → 验收 100%
- baseline compatible match = **43.8%** → 验收 100%
- 若用户指定 target_platform，Agent1/Orchestrator 必须用代码强制覆盖 LLM 输出