# Mgame 自动加次数任务 · Agent 接手指南

维护 Windows 上的手机辅助程序。程序通过 ADB 读取已授权安卓真机的界面，根据任务进度完成浏览、游玩、听书和短剧任务，并核对领币次数。面向使用者的步骤见 [使用手册](README.md)，阶段规则和实测记录见 [交接说明](交接说明.md)。

开始工作时先读 [项目状态](项目状态.md)，核对最近回测、证据路径和待验证项。

本项目生成的计划、日志、截图及其他临时文件均保存在仓库目录内。计划用 `.planning/`，运行证据用 `runs/`。

## 接手后先做什么

1. 在仓库根目录检查 `git status --short --branch` 和 `git log -1`，查看用户是否已有未提交修改。11 月 21 日回测以 `e1215b230dfcd37febbee8ff14aa2a3c00f6257d` 为基线，现场修复及后续整理已纳入 Git 跟踪。再次同步前，应核对并保留本地修改。用 `adb devices -l` 确认手机在线，核对已解锁、1080×2400 竖屏和应用当前画面。只有一台已授权真机时程序会自行选择，多台时用 `-Serial` 指定。
2. 有历史截图时先用 `-Image <截图路径>` 离线检查。手机停在「我的」「领币页」或「加次数」时，可先运行下方的只读命令。只读模式只识别当前画面，屏幕之外的任务不在预览结果里。
3. 用户要求真机回测时，按当前任务状态续跑。优先通过程序保存的截图和日志判断执行效果。当前结论以 [项目状态](项目状态.md) 中的日期和证据为准。

```powershell
# 识别当前画面
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1

# 单独回测听书
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Task listen30 -Execute

# 单独回测跳转登录 APP 领奖励，也可双击跳转登录APP领奖励.cmd
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Task appjump -Execute

# 依次执行八个日常阶段
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Task all -Execute

# 单独导航到游戏中心
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -OpenGameCenter
```

## 改哪里、怎么验证

- [coin_helper.py](coin_helper.py) 中的 `run_stages` 固定顺序。三个重复浏览任务在 `finish_task`，60 秒游戏在 `finish_games`，其余计时任务在 `finish_browse10` 和 `timed_media`。启动参数由 [run.ps1](run.ps1) 转发，界面模板在 `templates/`，Windows OCR 在 `read-progress.ps1`。
- 独立的 `appjump` 由 `-Task appjump` 显式启动。`visit_app_jump` 确认目标包名进入前台后连续计时 10 秒，中断则重新计时。返回美团后，`finish_app_jump` 核对「可领取」和 `x/8`，暂无可用入口时最多等待并刷新两次，记录 `app_jump_settlement_wait`。当本轮已确认有效停留且刷新后仍无可用入口时，阶段最多重开美团一次，保留本轮已尝试的入口并记录 `app_jump_restart_mgame`。`app_jump_visit_elapsed` 只表示计时到达，结算结果以 `app_jump_result` 和 `app_jump_progress` 为准。真机回测和 8/8 证据见 [项目状态](项目状态.md)。
- 保留原任务的识别依据。浏览奖励按小字匹配。同一行出现下单奖励时，只处理浏览部分。短剧和听书在播放页计时。领奖前核对同一行的「可领奖」，领奖后核对实际次数增加。
- 遇到陌生页面时，用当前截图和前台活动确认页面类型与退出控件。若卡在广告或其他应用，先等当前运行停止，再用 `-ReturnMgame` 返回 Mgame 主界面。重新进入「加次数」后按实际进度续跑。
- 每次运行查看 `runs/<运行编号>/events.jsonl`。用 `stage_started` 找到出错阶段，用 `stopped` 或 `ad_recovery` 看原因。日志里的 `screenshot` 事件指向程序保存的截图，`screenshot_unavailable` 表示该画面未能保存。以 `task_complete`、`game60_confirmed` 或 `timed_confirmed` 结合截图核对结算。`stage_finished` 的 `elapsed_seconds` 可用于比较同阶段耗时，该事件也可能表示扫描后跳过。
- 提交前检查 `git diff --check`。代码有变化时先跑 Python 语法检查，再验证改动涉及的阶段。回测报告写明完成进度、实际到账次数、人工介入和日志路径。保留提交所需的局部模板，运行日志、完整截图和本机环境由 `.gitignore` 排除。

## 优化日志

### 2026-11-22

优化以 Python 程序自主执行和恢复为主。直接入口为 `python coin_helper.py --execute --task all`，也兼容既有 `run.ps1` 和 CMD 入口。解释器需安装 requirements.txt 中的依赖。本机已创建项目 `.venv` 并安装依赖，可用 `.\.venv\Scripts\python.exe` 直接执行或由启动器自动选取。11 月 21 日使用的相邻环境路径保留作历史记录。

- `run_stages` 对 `ForegroundChanged`、`CaptureUnavailable`、`LandingUnconfirmed`、`CountUnreadable` 增加阶段级恢复。每阶段最多执行 3 次，恢复时记录 `stage_recovery` 的 `attempt` 和 `reason`，保存截图后调用 `recover_mgame_main`，重新执行当前阶段并读取实际进度。此前阶段不会重新运行。内部原有按轮、按任务重试仍存在，各自遵守现有上限。
- `stage_finished.recoveries` 是阶段级恢复次数，结合 `elapsed_seconds` 对比效率。达到第三次失败、恢复主界面失败或其他停止条件时，会沿正常入口记录 `stopped` 并以退出码 2 退出。到账不符的普通 `Stop` 不会由阶段恢复自动重试。
- `wait_for_page` 的结算等待最多关闭 3 层已识别 WebView，每次成功匹配关闭后刷新等待截止时间。即使关闭无效，次数也有上限。
- 外部浏览、10 秒浏览、游戏桌面计时和媒体播放在确认目标后设定单调时钟截止时间，将取证截图耗时纳入等待。游戏只等待剩余时长。媒体结束前再次核对前台组件。后续比较耗时时应同时核对真实等待区间与奖励证据。
- 离线 `--image` 模式对所有任务统一调用 `check_size`，尺寸不符会返回错误，避免计时任务预览误报 0 项。

发布前执行 `python -m unittest discover -s tests -v`，以及 `git diff --check` 和 Python 编译。测试模拟阶段恢复、错误类型、已处理阶段跳过、广告页关闭上限、截图耗时与计时中断，不连接手机。用现场截图补查直播退出和听书签到浮层定位。此次优化尚未完整真机回测，下一轮继续验证听书完整播放和无需重启的整条流程，并对比约 43 分钟的历史整轮用时。

### 2026-11-21

本次真机回测从同步的 `e1215b2` 开始，首段与续跑合计约 43 分钟。浏览页面 5/5、好物 10/10、优惠活动 15/15；60 秒游戏 7 项，各 +3；浏览 10 秒 5 项，各 +2；20 秒游戏 4 项，依次 +1、+2、+2、+1；听书和短剧各 +2。最终截图为 229 次。首次运行在直播退出时中断，Agent 修改程序后重启一次全流程，随后按实际进度跳过已完成阶段，正常退出。

- 首段日志为 `runs/20261121-002049-919055/events.jsonl`，包含前三项浏览的进度和直播停止原因。完整现场图 `live-exit-diagnostic.png` 可用于退出键回归。
- 直播单独恢复记录为 `runs/20261121-003144-068178`。续跑日志为 `runs/20261121-003152-345517/events.jsonl`，以 `all_stages_finished` 和退出码 0 结束。最终次数截图为 `screen-097-stage-end.jpg`。
- 听书现场 `screen-090-ad-recovery.jpg` 显示签到浮层下移。该轮未确认播放，自动恢复后平台显示可领奖，已核对 225 → 227。短剧有 `timed_media_started` 的连续 62 秒计时，到账 227 → 229。

本次启动通过 `-Python` 指定相邻项目的环境。本地可复用以下命令，先运行预览，再按用户授权执行任务。

```powershell
$python = (Resolve-Path '..\Mgame-FCash-auto-tasks\.venv\Scripts\python.exe').Path
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Python $python
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Python $python -Task all -Execute
```

后续优化在真机任务已完成后做离线回归。

- `Vision.match(..., white_mask=True)` 复用模板匹配和候选唯一性检查，以 220 的阈值提取白色控件。直播仍先使用原灰度模板，失败后在固定右上角区域以 0.94 匹配白色 X。点击前复核直播组件，识别失败抛 `LandingUnconfirmed`，由 `finish_task` 已有的最多四次尝试恢复和核对进度。
- 直播点击记为 `live_close_tapped`。该事件只证明已发送点击，退出结果结合后续前台组件和结算确认。
- 听书签到标题搜索范围扩展至 y=475～700，关闭键纵向区域由匹配到的标题决定。已识别浮层但找不到关闭键时，明确记录失败并转入有限恢复流程。点击前检查阅读器组件，`novel-popup-close-attempt` 保存现场，`novel_popup_close_tapped` 记录点击。
- `novel_playback_confirmed` 表示看到了暂停控件，`timed_media_started` 表示开始等待，新增 `timed_media_completed` 表示前台计时循环完整结束。`timed_confirmed` 继续只记录到账。旧日志可能没有新事件，回看历史时按当时版本解释。

下次任务重置后优先跑 `-Task listen30 -Execute`，核对关闭签到浮层、播放确认、连续 32 秒、退出及 +2 到账。直播恢复调整和听书浮层调整均需补下一轮真机覆盖。手机操作沿用程序执行，Agent 根据保存截图修改后续跑。

### 2026-11-20

- `finish_task` 在每轮按进度确认，末轮才额外读取次数。若末轮恢复后已找不到任务行，先确认完整扫描过列表，再核对次数增量，用 `final_row_removed` 记录证据。普通轮次可用 `progress` 和 `task_complete` 核对。
- `Phone.frame` 对短暂无图做有限重试。应用锁导致的前台切换交给已有广告恢复流程，先用 `ad_recovery` 截图判断落地页，再核对实际进度。
- `confirm_reward_count` 统一 60 秒游戏和其他计时奖励的到账判断。用 `reward_count_retry` 中的 `expected`、`observed` 和 `attempt` 对照独立编号的 OCR 图片。末轮浏览的行消失校验仍按该阶段自己的奖励核对。
- `open_game_center` 复用 Windows OCR 的坐标输出。用 `navigation-ocr-*.png` 对照 `game_center_link_tapped`、`game_center_desktop_prompt_tapped`、`game_center_ready`。只有首页标题和栏目均识别到，且桌面入口提示已消失，才记录就绪。两条手机流程按顺序接力。
- 11 月 20 日真机分段跑完 8 个阶段，最终次数 261。听书直接结算 +2，仍需验证插屏恢复后完整播放。后续整理以历史截图和模拟流程验证，下一次真机重置时重点核对单次全流程、末轮恢复和听书计时。
