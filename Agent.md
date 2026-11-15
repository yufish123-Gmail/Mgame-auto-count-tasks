# Mgame 自动加次数任务 · Agent 接手指南

维护 Windows 上的手机辅助程序。程序通过 ADB 读取已授权安卓真机的界面，根据任务进度完成浏览、游玩、听书和短剧任务，并核对领币次数。面向使用者的步骤见 [使用手册](README.md)，阶段规则和实测记录见 [交接说明](交接说明.md)。

## 接手后先做什么

1. 在仓库根目录检查 `git status --short --branch`，查看用户是否已有未提交修改。用 `adb devices -l` 确认手机在线，核对已解锁、1080×2400 竖屏和应用当前画面。只有一台已授权真机时程序会自行选择，多台时用 `-Serial` 指定。
2. 有历史截图时先用 `-Image <截图路径>` 离线检查。手机停在「我的」「领币页」或「加次数」时，可先运行下方的只读命令。只读模式只识别当前画面，屏幕之外的任务不在预览结果里。
3. 用户要求真机回测时，按当前任务状态续跑。下次重置后的首轮回测应从「我的」进入列表并执行完整流程，重点验证听小说能否自动关闭签到浮层、确认播放、等待 32 秒、退出并领奖。上一轮听书的播放确认和退出有人工介入。

```powershell
# 识别当前画面
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1

# 单独回测听书
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Task listen30 -Execute

# 依次执行全部阶段
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Task all -Execute
```

## 改哪里、怎么验证

- [coin_helper.py](coin_helper.py) 中的 `run_stages` 固定顺序。三个重复浏览任务在 `finish_task`，60 秒游戏在 `finish_games`，其余计时任务在 `finish_browse10` 和 `timed_media`。启动参数由 [run.ps1](run.ps1) 转发，界面模板在 `templates/`，Windows OCR 在 `read-progress.ps1`。
- 保留原任务的识别依据。浏览奖励按小字匹配。同一行出现下单奖励时，只处理浏览部分。短剧和听书在播放页计时。领奖前核对同一行的「可领奖」，领奖后核对实际次数增加。
- 遇到陌生页面时，用当前截图和前台活动确认页面类型与退出控件。若卡在广告或其他应用，先等当前运行停止，再用 `-ReturnMgame` 返回 Mgame 主界面。重新进入「加次数」后按实际进度续跑。
- 每次运行查看 `runs/<运行编号>/events.jsonl`。用 `stage_started` 找到出错阶段，用 `stopped` 看原因。以 `task_complete`、`game60_confirmed` 或 `timed_confirmed` 结合手机页面核对结算。`stage_finished` 也可能表示扫描后跳过该阶段。
- 提交前检查 `git diff --check`。代码有变化时先跑 Python 语法检查，再验证改动涉及的阶段。回测报告写明完成进度、实际到账次数、人工介入和日志路径。保留提交所需的局部模板，运行日志、完整截图和本机环境由 `.gitignore` 排除。
