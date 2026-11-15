# 自动加次数任务

平台标识为 `Mgame`。

首次在 Windows 电脑上使用时，在项目目录打开 PowerShell，用已安装的 Python 建立环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

电脑需要已安装 ADB（Android SDK Platform Tools）和 Windows 简体中文 OCR。当前模板按 1080×2400 竖屏校准。

## 手机怎么准备

1. 等Mgame任务重置。用数据线连接手机和电脑，解锁手机。若手机弹出 USB 调试授权，点「允许」。
2. 打开Mgame，点右下角「我的」。也可以直接停在「领币页 → 加次数」列表。
3. 在电脑上打开项目目录，双击 **自动加次数任务.cmd**。运行时让手机保持解锁，等电脑提示各阶段处理完毕或显示停止原因。

游戏计时中会暂时回到手机桌面，等程序自己切回Mgame。运行时不要手动操作手机。结束后到「领币页 → 加次数」核对进度和领币次数。

## 执行顺序

1. 浏览页面领奖励（3 秒）
2. 浏览好物（5 秒）
3. 浏览优惠活动（10 秒）
4. 玩游戏（60 秒，+3）
5. 小字写「浏览 10 秒」的闪电奖励
6. 小字写「玩 20 秒」的闪电奖励
7. 听小说（30 秒）
8. 看短剧（60 秒）

程序按Mgame当前显示的进度接着跑。某项已做完或暂未列出时，以窗口里的结算和跳过提示为准。

## 中途停住

先记下窗口里的阶段、停止原因和运行记录路径。把手机恢复到Mgame「我的」或「加次数」，再双击运行。若卡在其他应用或插屏，等上一次运行停止后，在电脑 PowerShell 运行以下命令返回Mgame：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -ReturnMgame
```

给下一位 Codex 或其他 Agent 的说明见 [交接说明](交接说明.md)。
