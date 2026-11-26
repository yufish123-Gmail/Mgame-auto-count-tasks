from __future__ import annotations

import argparse
import json
import msvcrt
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "templates"
MGAME = "com.sankuai.meituan"
MGAME_MAIN = MGAME + "/com.meituan.android.pt.homepage.activity.MainActivity"
APP_LOCK = "com.miui.securitycenter"
PROMO_ACTIVITY = "com.meituan.android.knb.core.StandardKnbActivity"
PROMO_SEARCH = MGAME + ".search.result.SearchResultActivity"
PROMO_WEBVIEW = "com.sankuai.titans.adapter.mtapp.KNBWebViewActivity"
VERIFICATION_ACTIVITY = "com.meituan.android.yoda.activity.YodaRouterTransparentActivity"
BROWSE_VERIFICATION_ACTIVITIES = {
    VERIFICATION_ACTIVITY, "com.meituan.android.yoda.activity.YodaConfirmActivity",
}
PROMO_LIVE = "com.dianping.live.live.mrn.MLiveMRNActivity"
PROMO_GROUP = "com.meituan.android.mtgb.business.main.MTGMainActivity"
PROMO_ATTRACTION = "com.meituan.traveltools.mrncontainer.HTMRNBaseActivity"
AD_WEBVIEW = "com.ubix.ssp.open.comm.UBiXWebViewActivity"
PROMO_PORTRAIT_AD = "com.qq.e.ads.PortraitADActivity"
PROMO_VIDEO_AD = MGAME + ".msv.page.activity.MSVPageActivity"
PROMO_SIGMOB_AD = "com.sigmob.sdk.base.common.PortraitAdActivity"
DOUYIN_PACKAGE = "com.ss.android.ugc.aweme"
DOUYIN_OPEN_BUTTON = (215, 2120, 870, 2230)
PROMO_VIDEO_END_BUTTON = (433, 1325, 647, 1434)
PROMO_EXTERNAL_APPS = {"com.dianping.v1"}
GOODS_EXTERNAL_APPS = {
    "com.qiyi.video", "com.UCMobile", "com.tencent.qqlive", DOUYIN_PACKAGE,
    "com.ss.android.article.lite", "cn.missevan", "com.sina.weibo",
    "com.quark.browser", "com.taobao.idlefish",
}
GAME_ACTIVITY = "com.meituan.android.mgc.container.MGCGameActivity"
GAME_AD = "com.windmill.sdk.widget.InterstitialView_4012001"
GAME_AD_COMPACT = "com.windmill.sdk.widget.InterstitialView_4012003"
GAME60_AD_COMPONENTS = ([MGAME, GAME_AD], [MGAME, GAME_AD_COMPACT])
GAME_COMPONENTS = ([MGAME, GAME_ACTIVITY], *GAME60_AD_COMPONENTS)
GAME_WAIT_SECONDS = 62
GAME_FOREGROUND_RETRY_SECONDS = GAME_WAIT_SECONDS + 8
APP_JUMP_WAIT_SECONDS = 10
APP_JUMP_TARGETS = {
    "百度极速版": "com.baidu.searchbox.lite",
    "QQ音乐": "com.tencent.qqmusic",
    "UC浏览器": "com.UCMobile",
    "芒果TV": "com.hunantv.imgo.activity",
    "猫耳FM": "cn.missevan",
    "书旗小说": "com.shuqi.controller",
    "头条极速": "com.ss.android.article.lite",
    "百度地图": "com.baidu.BaiduMap",
}
LANDING_APPS = {
    "com.android.browser", "com.alibaba.wireless", "com.kuaishou.nebula",
    "com.tmall.wireless", "com.taobao.taobao", "com.baidu.netdisk",
    "com.aliyun.tongyi", "com.baidu.BaiduMap", "com.jingdong.app.mall",
}
SIZE = (1080, 2400)
CHANCES = (62, 396, 148, 458)


@dataclass(frozen=True)
class Task:
    key: str
    name: str
    seconds: int
    reward: int
    title: str
    row_reward: str
    cta: str | None
    badge: str


TASKS = (
    Task("page", "浏览页面领奖励", 3, 2, "task", "task_reward", "browse_cta", "reward_two"),
    Task("goods", "浏览好物5秒", 5, 3, "good_task", "good_task_reward", "good_cta", "reward_three"),
    Task("promo", "浏览优惠活动", 10, 3, "promo_task", "promo_task_reward", None, "reward_three"),
)


class Stop(RuntimeError):
    pass


class RoundUnsettled(Stop):
    pass


class VerificationRequired(Stop):
    pass


class ForegroundChanged(Stop):
    pass


class CaptureUnavailable(Stop):
    pass


class LandingUnconfirmed(Stop):
    pass


class TaskRowMoved(Stop):
    pass


class CountUnreadable(Stop):
    pass


class DeviceDisconnected(Stop):
    pass


def run_command(*arguments, tool="ADB"):
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Stop(f"{tool} 执行失败：{exc}") from exc
    error = result.stderr.decode("utf-8", errors="replace").strip()
    output = result.stdout.decode("utf-8", errors="replace") if arguments[-2:] != ("screencap", "-p") else ""
    adb_error = tool == "ADB" and ("SecurityException" in error + output or "Error:" in output)
    if result.returncode or adb_error:
        message = f"{tool} 执行失败：{error or output.strip() or result.returncode}"
        disconnected = r"device(?: '[^']+')? (?:not found|offline|unauthorized)|no devices/emulators found"
        if tool == "ADB" and re.search(disconnected, error, re.I):
            raise DeviceDisconnected(message)
        raise Stop(message)
    return result.stdout


def find_adb():
    local = os.environ.get("LOCALAPPDATA", "")
    candidate = Path(local) / "Android/Sdk/platform-tools/adb.exe"
    path = shutil.which("adb") or (str(candidate) if local and candidate.is_file() else None)
    if path is None:
        raise Stop("找不到 adb.exe。请确认 Android SDK Platform Tools 已安装。")
    return path


def decode(data):
    if not data:
        raise CaptureUnavailable("截图暂不可用，手机可能正在切换页面。")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise CaptureUnavailable("截图不完整或无法解码。")
    return image


def load_image(path):
    return decode(path.read_bytes())


def save_image(path, image):
    ok, data = cv2.imencode(".png", image)
    if not ok:
        raise Stop("无法写入诊断截图。")
    path.write_bytes(data.tobytes())


class Phone:
    def __init__(self, serial=None):
        self.adb = find_adb()
        rows = run_command(self.adb, "devices", "-l").decode(errors="replace").splitlines()
        devices = [
            fields[0] for fields in map(str.split, rows)
            if len(fields) > 2 and fields[1] == "device"
            and any(field.startswith("usb:") or field.startswith("transport_id:") for field in fields[2:])
            and not fields[0].startswith("emulator-")
            and ":" not in fields[0]
            and not fields[0].endswith("._adb-tls-connect._tcp")
        ]
        if serial is None:
            if len(devices) != 1:
                raise Stop("请连接并授权一台 USB 真机。连接多台时请用 -Serial 指定。")
            serial = devices[0]
        if serial not in devices:
            raise Stop(f"指定真机 {serial} 未连接或未授权。")
        self.serial = serial

    def adb_call(self, *arguments):
        return run_command(self.adb, "-s", self.serial, *arguments)

    def wait_for_usb(self, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.adb_call("get-state").strip() == b"device":
                    self.foreground_component()
                    return
            except DeviceDisconnected:
                pass
            time.sleep(1)
        raise DeviceDisconnected("USB 调试连接未恢复，请重新连接并授权原手机。")

    def foreground_component(self):
        output = self.adb_call("shell", "dumpsys", "activity", "activities").decode(errors="replace")
        match = re.search(r"(?:topResumedActivity=|mResumedActivity:).*?\s([\w.]+/[\w.$]+)", output)
        if not match:
            return None, None
        package, activity = match[1].split("/", 1)
        return [package, package + activity if activity.startswith(".") else activity]

    def foreground(self):
        return self.foreground_component()[0]

    def require_mgame(self):
        if self.foreground() != MGAME:
            raise ForegroundChanged("请把Mgame放在前台。")

    def capture(self, *, calibrated=True):
        image = decode(self.adb_call("exec-out", "screencap", "-p"))
        width, height = image.shape[1::-1]
        if calibrated and (width, height) != SIZE:
            raise CaptureUnavailable(f"截图暂为 {width}×{height}，等待 1080×2400 竖屏恢复。")
        return image

    def frame(self):
        for attempt in range(4):
            self.require_mgame()
            try:
                image = self.capture()
            except CaptureUnavailable:
                if attempt == 3:
                    raise
                time.sleep(0.4)
                continue
            self.require_mgame()
            return image

    def tap(self, point):
        self.require_mgame()
        self.adb_call("shell", "input", "tap", *map(str, point))

    def scroll(self, down):
        self.require_mgame()
        start, end = (1650, 990) if down else (990, 1650)
        self.adb_call("shell", "input", "swipe", "750", str(start), "750", str(end), "800")

    def return_to_mgame(self):
        self.adb_call(
            "shell", "am", "start", "-a", "android.intent.action.MAIN", "-c",
            "android.intent.category.LAUNCHER", "-f", "0x10200000", "-n", MGAME_MAIN,
        )

    def back(self):
        self.require_mgame()
        self.adb_call("shell", "input", "keyevent", "4")

    def home(self, *, recovery=False):
        if not recovery:
            self.require_mgame()
        output = self.adb_call(
            "shell", "cmd", "package", "resolve-activity", "--brief", "-a",
            "android.intent.action.MAIN", "-c", "android.intent.category.HOME",
        ).decode(errors="replace")
        launcher = re.search(r"(?m)^([\w.]+)/[\w.$]+\s*$", output)
        if launcher is None or launcher[1] == MGAME:
            raise Stop("无法确认手机的默认桌面。")
        self.adb_call("shell", "input", "keyevent", "KEYCODE_HOME")
        return launcher[1]

    def recover_mgame_main(self, vision, trace):
        launcher = self.home(recovery=True)
        deadline = time.monotonic() + 5
        while self.foreground() != launcher:
            if time.monotonic() >= deadline:
                raise Stop("未确认回到手机桌面，请检查手机是否已解锁。")
            time.sleep(0.4)
        trace.record("recovery_home")
        self.adb_call("shell", "input", "keyevent", "KEYCODE_APP_SWITCH")
        time.sleep(0.8)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and self.foreground() == launcher:
            try:
                image = self.capture()
            except CaptureUnavailable:
                break
            icon = vision.match(image, "mgame_recent", (20, 235, 1061, 2150), 0.94)
            if icon and self.foreground() == launcher:
                # This launcher places the task preview immediately below its app icon.
                x, _ = center(icon)
                self.adb_call("shell", "input", "tap", str(x), str(icon[3] + 85))
                trace.record("recovery_recent_card")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and self.foreground() != MGAME:
                    time.sleep(0.4)
                break
            time.sleep(0.4)
        main = MGAME_MAIN.split("/", 1)
        if self.foreground_component() != main:
            trace.record("recovery_open_main")
            # NEW_TASK | CLEAR_TOP | SINGLE_TOP brings the root activity forward.
            self.adb_call(
                "shell", "am", "start", "-W", "-a", "android.intent.action.MAIN", "-c",
                "android.intent.category.LAUNCHER", "-f", "0x34000000", "-n", MGAME_MAIN,
            )
        deadline = time.monotonic() + 8
        while self.foreground_component() != main:
            if time.monotonic() >= deadline:
                if self.foreground_component() in GAME_COMPONENTS:
                    trace.snapshot(self, "stuck-game-before-restart")
                    trace.record("recovery_stuck_game_restart")
                    self.restart_mgame(vision, trace)
                    break
                raise Stop("切换后未确认Mgame主界面已打开。")
            time.sleep(0.4)
        trace.record("recovery_main_confirmed")

    def restart_mgame(self, vision, trace):
        self.require_mgame()
        self.adb_call("shell", "am", "force-stop", MGAME)
        self.return_to_mgame()
        trace.record("recovery_cold_restart")
        main = MGAME_MAIN.split("/", 1)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.foreground_component() == main:
                try:
                    image = self.frame()
                except (ForegroundChanged, CaptureUnavailable):
                    continue
                if vision.identify(image)[0] == "profile":
                    return
                top = 2180
                raw = read_ocr(trace, image[top:2355], "recovery-home-tabs.png", layout=True)
                tabs = [(re.sub(r"\s+", "", line["text"]), line["box"]) for line in json.loads(raw)]
                profile = [box for label, box in tabs if label == "我的" and 880 < box[0] < 1030]
                if len(profile) == 1 and any(label == "推荐" for label, _ in tabs):
                    if self.foreground_component() == main:
                        x, y = center(tuple(round(value) for value in profile[0]))
                        self.tap((x, y + top))
                        trace.record("recovery_profile_tab_tapped", point=(x, y + top))
                        wait_for_page(self, vision, {"profile"}, timeout=8, trace=trace)
                        return
            time.sleep(0.5)
        raise LandingUnconfirmed("重启后未确认Mgame的「我的」页面。")


def check_size(image):
    if image.shape[1::-1] != SIZE:
        raise Stop(f"截图尺寸必须为 {SIZE[0]}×{SIZE[1]} 竖屏。")


def crop(image, box):
    left, top, right, bottom = box
    return image[top:bottom, left:right]


@lru_cache(maxsize=1)
def load_templates():
    return {
        path.stem: cv2.cvtColor(load_image(path), cv2.COLOR_BGR2GRAY)
        for path in ASSETS.glob("*.png")
    }


class Vision:
    def __init__(self, task=TASKS[0]):
        self.task = task
        self.templates = load_templates()

    def match(self, image, name, region, minimum=0.90, competing=None, *, white_mask=False):
        template = self.templates[name]
        view = cv2.cvtColor(crop(image, region), cv2.COLOR_BGR2GRAY)
        if white_mask:
            # Isolate white controls from a changing video background.
            _, view = cv2.threshold(view, 220, 255, cv2.THRESH_BINARY)
            _, template = cv2.threshold(template, 220, 255, cv2.THRESH_BINARY)
        height, width = template.shape
        if height > view.shape[0] or width > view.shape[1]:
            return None
        scores = cv2.matchTemplate(view, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < minimum:
            return None
        if competing:
            other = self.templates[competing]
            if white_mask:
                _, other = cv2.threshold(other, 220, 255, cv2.THRESH_BINARY)
            other_score = cv2.minMaxLoc(cv2.matchTemplate(view, other, cv2.TM_CCOEFF_NORMED))[1]
            if score < other_score + 0.015:
                return None
        scores[max(0, y - height // 2):y + height // 2 + 1,
               max(0, x - width // 2):x + width // 2 + 1] = -1
        if cv2.minMaxLoc(scores)[1] >= score - 0.04:
            return None
        left, top = x + region[0], y + region[1]
        return (left, top, left + width, top + height)

    def task_title(self, image):
        return self.match(image, self.task.title, (175, 705, 565, 2300))

    def promo_five_second_reward(self, image, title_y):
        return self.match(image, "good_task_reward", (180, title_y + 38, 575, title_y + 136))

    def task_wait_seconds(self, image):
        if self.task.key == "promo":
            title = self.task_title(image)
            if title and self.promo_five_second_reward(image, title[1]):
                return 5
        return self.task.seconds

    def promo_button(self, image, trace):
        # Require the observed ad dialog layout, then read its instruction.
        close = self.match(image, "ad_close", (479, 1675, 603, 1890))
        if close is None:
            return None
        y = center(close)[1] - 256
        region = (155, y - 48, 925, y + 48)
        pixels = crop(image, region)
        yellow = (pixels[:, :, 2] > 200) & (pixels[:, :, 1] > 165) & (pixels[:, :, 0] < 130)
        if float(np.mean(yellow)) < 0.70:
            return None
        text = read_ocr(trace, pixels, "promo-ad-button.png")
        if not re.fullmatch(r"点击并浏览(?:5|10)秒领奖励", re.sub(r"\s+", "", text)):
            return None
        return region

    def identify(self, image):
        check_size(image)
        other = TASKS[1] if self.task == TASKS[0] else TASKS[0]
        if self.match(image, "reward_title", (275, 665, 802, 880), 0.86):
            if self.match(image, self.task.badge, (672, 900, 850, 1055), 0.89, competing=other.badge):
                if self.match(image, "reward_final", (390, 1525, 690, 1670)):
                    return "reward_final", None
                return "reward", None
            return "unknown", None
        target = self.match(image, self.task.cta, (245, 1390, 831, 1640), competing=other.cta) if self.task.cta else None
        if target and self.bright(crop(image, target), 185):
            return "ad", target
        if self.task.key == "promo" and (
            self.match(image, "promo_member", (80, 130, 390, 248))
            or self.match(image, "promo_weekly", (270, 128, 815, 248))
            or self.match(image, "promo_hotel", (270, 128, 815, 248))
            or self.match(image, "promo_group", (84, 105, 286, 204))
            or self.match(image, "promo_takeout", (143, 117, 397, 215))
        ):
            return "promo_landing", None
        if self.match(image, "panel", (270, 555, 800, 688)) and self.bright(image[565:585, 180:260], 225):
            title = self.task_title(image)
            if title:
                y = title[1]
                if y + 136 > 2350:
                    return "list", None
                reward = self.match(image, self.task.row_reward, (180, y + 38, 575, y + 136))
                if reward is None and self.task.key == "promo":
                    reward = self.promo_five_second_reward(image, y) or self.match(
                        image, "promo_click_reward", (180, y + 38, 650, y + 136)
                    )
                button = self.match(image, "go", (850, max(700, y - 12), 1030, y + 99))
                if reward and button:
                    return "task", button
                return "task_unavailable", None
            return "list", None
        if self.match(image, "coins_title", (65, 120, 260, 215)):
            target = self.match(image, "add_chances", (38, 735, 218, 872))
            return ("coins", target) if target and self.bright(crop(image, target), 185) else ("unknown", None)
        if self.match(image, "profile_tab", (900, 2250, 1045, 2350)):
            target = self.match(image, "coins_entry", (850, 870, 1055, 1180))
            if target:
                return "profile", target
        return "unknown", None

    @staticmethod
    def bright(image, level):
        return float(np.median(image.max(axis=2))) >= level


def center(box):
    return ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)


def read_ocr(trace, image, filename, *, layout=False, reuse=False):
    path = trace.directory / filename
    if reuse:
        previous = trace.ocr_samples.get(filename)
        if previous is not None and previous[0] == layout and np.array_equal(previous[1], image):
            trace.record("ocr_reused", file=filename)
            return previous[2]
    trace.ocr_samples.pop(filename, None)
    save_image(path, image)
    arguments = ("-Layout",) if layout else ()
    data = run_command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", str(ROOT / "read-progress.ps1"), "-Image", str(path), *arguments,
                       tool="Windows OCR")
    result = data.decode("utf-8", errors="replace").strip()
    if reuse:
        trace.ocr_samples[filename] = (layout, image.copy(), result)
    return result


def read_progress(image, vision, trace, label):
    title = vision.task_title(image)
    if title is None:
        raise Stop("画面中找不到任务名称。")
    left, top, right, bottom = title
    inset = 17 if vision.task.key == "promo" else 8
    snippet = crop(image, (right + inset, top - 4, min(right + 175, 820), bottom + 4))
    path = trace.directory / f"progress-{label}.png"
    gray = cv2.cvtColor(snippet, cv2.COLOR_BGR2GRAY)
    blue, green, red = cv2.split(snippet)
    # The orange task decoration can overlap a dark progress digit.
    tinted = (red.astype(np.int16) - blue > 45) & (green.astype(np.int16) - blue > 12)
    gray[tinted] = 255
    enlarged = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(enlarged, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    padded = cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    previous = trace.last_progress
    if previous is not None:
        task, pixels, done, total, source = previous
        if task == vision.task.key and np.array_equal(pixels, padded):
            trace.record("progress", completed=done, total=total, ocr_reused_from=source)
            return done, total
    source = path.name
    output = read_ocr(trace, padded, path.name)
    values = re.findall(r"(?<!\d)(\d{1,2})\s*[/／]\s*(\d{1,2})(?!\d)", output)
    values = [value for value in values if 0 <= int(value[0]) <= int(value[1]) <= 30 and int(value[1]) > 0]
    if not values:
        points = cv2.findNonZero(255 - binary)
        if points is not None:
            x, y, width, height = cv2.boundingRect(points)
            tight = binary[y:y + height, x:x + width]
            for scale in (0.75, 1.0, 0.5):
                resized = cv2.resize(tight, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                retry = cv2.copyMakeBorder(resized, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
                source = f"progress-{label}-retry-{scale}.png"
                output = read_ocr(trace, retry, source)
                values = re.findall(r"(?<!\d)(\d{1,2})\s*[/／]\s*(\d{1,2})(?!\d)", output)
                values = [value for value in values if 0 <= int(value[0]) <= int(value[1]) <= 30 and int(value[1]) > 0]
                if len(values) == 1:
                    break
    if len(values) != 1:
        raise Stop(f"未能读取任务进度，请检查 {path.name}。")
    done, total = map(int, values[0])
    if not 0 <= done <= total <= 30 or total == 0:
        raise Stop(f"无法确认任务进度 {done}/{total}。")
    trace.last_progress = (vision.task.key, padded, done, total, source)
    trace.record("progress", completed=done, total=total)
    return done, total


class Trace:
    def __init__(self, mode):
        self.directory = ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.directory.mkdir(parents=True)
        self.stage = None
        self.screenshot_index = 0
        self.last_progress = None
        self.ocr_samples = {}
        self.record("start", mode=mode)

    def record(self, event, **fields):
        entry = {"time": datetime.now().isoformat(timespec="seconds"), "event": event, **fields}
        if self.stage is not None:
            entry.setdefault("stage", self.stage)
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def snapshot(self, phone, state, *, image=None, component=None):
        try:
            component = component if component is not None else phone.foreground_component()
            image = phone.capture(calibrated=False) if image is None else image
            self.screenshot_index += 1
            filename = f"screen-{self.screenshot_index:03d}-{state}.jpg"
            scaled = cv2.resize(image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
            ok, data = cv2.imencode(".jpg", scaled, [cv2.IMWRITE_JPEG_QUALITY, 83])
            if not ok:
                raise Stop("无法编码诊断截图。")
            (self.directory / filename).write_bytes(data.tobytes())
            self.record("screenshot", state=state, component=component, file=filename)
        except (Stop, OSError, cv2.error) as exc:
            self.record("screenshot_unavailable", state=state, component=component, reason=str(exc))

    def evidence(self, image, state, box, filename):
        regions = {
            "profile": (850, 870, 1055, 1180),
            "coins": (0, 690, 255, 855),
            "task": (0, max(699, box[1] - 48), 1048, min(2350, box[3] + 68)) if box else None,
            "ad": (140, 1450, 940, 1625),
            "reward": (325, 695, 825, 1030),
            "reward_final": (325, 720, 825, 1060),
        }
        region = regions.get(state)
        if not region:
            return
        image = image.copy()
        if state == "profile":
            image[973:1007, 895:1000] = 0
        if box:
            cv2.rectangle(image, box[:2], box[2:], (0, 0, 255), 3)
            cv2.drawMarker(image, center(box), (0, 0, 255), cv2.MARKER_CROSS, 24, 3)
        save_image(self.directory / filename, crop(image, region))


def observe(image, vision, trace=None):
    state, box = vision.identify(image)
    if state == "unknown" and vision.task.key == "promo" and trace:
        box = vision.promo_button(image, trace)
        if box:
            return "ad", box
    return state, box


def is_travel_promo(image, trace):
    title = read_ocr(trace, crop(image, (345, 268, 745, 341)), "promo-travel-title.png")
    return "境外酒店" in re.sub(r"\s+", "", title)


def is_ranked_promo(image, trace):
    banner = crop(image, (120, 295, 1000, 460))
    blue, _, red = cv2.split(banner)
    if np.mean((blue > 145) & (red > 70)) < 0.45:
        return False
    title = read_ocr(trace, banner, "promo-rank-title.png")
    return bool(re.search(r"好评榜|人气榜|热销榜|排行榜", re.sub(r"\s+", "", title)))


def promo_fullscreen_button(image, activity, trace):
    if activity == PROMO_PORTRAIT_AD:
        banner, button = (125, 50, 690, 141), (370, 1610, 710, 1740)
        instruction, label = "app体验15秒", "了解更多"
    elif activity == PROMO_VIDEO_AD:
        banner, button = (25, 169, 448, 256), (430, 2130, 640, 2230)
        instruction, label = "点击立得丰富奖励", "启动"
    elif activity == PROMO_SIGMOB_AD:
        banner, button = (290, 174, 616, 255), (400, 1478, 660, 1570)
        instruction, label = "浏览6秒领取奖励", "立即前往"
    else:
        return None
    text = read_ocr(trace, crop(image, banner), "promo-fullscreen-banner.png")
    instruction_matches = instruction in re.sub(r"\s+", "", text).lower()
    if instruction_matches:
        text = read_ocr(trace, crop(image, button), "promo-fullscreen-button.png")
        if re.sub(r"\s+", "", text) == label:
            return button
    if activity == PROMO_VIDEO_AD:
        card = crop(image, (370, 1091, 710, 1556))
        text = read_ocr(trace, card, "promo-video-end-card.png")
        compact = re.sub(r"\s+", "", text)
        if "启动" in compact and "重新播放" in compact and any(
            app in compact for app in ("百度地图", "快手极速版")
        ):
            return PROMO_VIDEO_END_BUTTON
    if instruction_matches and activity == PROMO_PORTRAIT_AD:
        app = read_ocr(trace, crop(image, (440, 1900, 645, 1988)), "promo-fullscreen-app.png")
        if re.sub(r"\s+", "", app) != "抖音":
            return None
        text = read_ocr(trace, crop(image, DOUYIN_OPEN_BUTTON), "promo-fullscreen-button.png")
        if re.sub(r"\s+", "", text) == "点击打开或下载第三方应用":
            return DOUYIN_OPEN_BUTTON
    return None


def close_ad_webview(phone, vision, image, trace):
    if phone.foreground_component() != [MGAME, AD_WEBVIEW]:
        return False
    header = (340, 112, 745, 233)
    if vision.match(image, "ad_web_error", header):
        state = "ad-web-error"
    elif vision.match(image, "ad_web_store", header, 0.88):
        state = "ad-web-store"
    else:
        return False
    close = vision.match(image, "ad_web_close", (150, 112, 263, 228))
    if close is None:
        return False
    phone.tap(center(close))
    trace.record("ad_webview_closed", state=state, point=center(close))
    trace.snapshot(phone, state, image=image, component=[MGAME, AD_WEBVIEW])
    return True


def wait_for_page(phone, vision, wanted, timeout=10, trace=None, *, settling=False):
    deadline = time.monotonic() + timeout
    state = "unknown"
    entry_failed = False
    closed_webviews = 0
    returned_from_lock = False
    qqlive_return_tapped = False
    while time.monotonic() < deadline:
        try:
            image = phone.frame()
        except ForegroundChanged:
            package = phone.foreground()
            if settling and package == APP_LOCK and not returned_from_lock:
                trace.snapshot(phone, "settling-app-lock")
                phone.return_to_mgame()
                returned_from_lock = True
                wanted = wanted | {"ad"}
                deadline = time.monotonic() + timeout
            if settling and package == "com.tencent.qqlive" and not qqlive_return_tapped:
                qqlive_return_tapped = tap_qqlive_return(phone, vision, trace)
                if qqlive_return_tapped:
                    deadline = time.monotonic() + timeout
            if "promo_landing" in wanted and package in PROMO_EXTERNAL_APPS:
                return None, "promo_external", None
            time.sleep(0.45)
            continue
        except CaptureUnavailable:
            time.sleep(0.45)
            continue
        if "ad" in wanted and not entry_failed:
            failed = vision.match(image, "entry_failed", (290, 1090, 795, 1275), 0.87)
            if failed:
                if trace:
                    save_image(trace.directory / "entry-failed.png", crop(image, failed))
                    trace.record("entry_rejected")
                entry_failed = True
                # Give a late page transition time to arrive after the reported error.
                deadline = min(deadline, time.monotonic() + 3)
        state, box = observe(image, vision, trace)
        if state == "unknown" and "promo_landing" in wanted:
            component = phone.foreground_component()
            if component in ([MGAME, PROMO_ACTIVITY], [MGAME, PROMO_SEARCH]) or (
                component == [MGAME, PROMO_GROUP]
                and vision.match(image, "promo_group_home", (80, 115, 550, 210))
            ):
                state = "promo_landing"
            elif component == [MGAME, PROMO_WEBVIEW] and is_travel_promo(image, trace):
                return image, "promo_travel", None
            elif component == [MGAME, PROMO_WEBVIEW] and vision.match(image, "promo_lowprice", (75, 110, 360, 230), 0.90):
                return image, "promo_lowprice", None
            elif component == [MGAME, PROMO_LIVE]:
                return image, "promo_live", None
            elif component[0] == MGAME and trace and component[1] in {PROMO_PORTRAIT_AD, PROMO_VIDEO_AD, PROMO_SIGMOB_AD}:
                if promo_fullscreen_button(image, component[1], trace):
                    return image, "promo_fullscreen_ad", None
            elif component == [MGAME, GAME_ACTIVITY] and vision.match(
                image, "promo_farm", (121, 755, 620, 958)
            ):
                return image, "promo_farm", None
            elif component == [MGAME, PROMO_ATTRACTION] and vision.match(
                image, "promo_attraction", (22, 975, 276, 1050)
            ):
                return image, "promo_attraction", None
            elif component[0] == MGAME and is_ranked_promo(image, trace):
                return image, "promo_rank", None
        if state in wanted:
            return image, state, box
        if state == "unknown" and settling and closed_webviews < 3:
            if close_ad_webview(phone, vision, image, trace):
                closed_webviews += 1
                deadline = time.monotonic() + timeout
        time.sleep(0.45)
    if entry_failed:
        raise LandingUnconfirmed("Mgame提示「操作失败，请稍后重试」，未确认本轮开始。")
    raise LandingUnconfirmed(f"等待页面超时，预期 {', '.join(sorted(wanted))}，最后识别为 {state}。")


def tap_verified(phone, vision, trace, expected, label="", progress=None):
    image = phone.frame()
    state, box = observe(image, vision, trace)
    if state != expected or box is None:
        if expected == "task":
            raise LandingUnconfirmed(f"点击前页面已变化：{state}。")
        raise Stop(f"点击前页面已变化：{state}。")
    if progress is not None and read_progress(image, vision, trace, f"{label}-before-tap") != progress:
        raise Stop("点击前任务进度已变化。")
    trace.evidence(image, state, box, f"click-{state}{label}.png")
    trace.record("tap", state=state, point=center(box))
    phone.tap(center(box))
    return image


def dismiss_ad(phone, vision, trace):
    image = phone.frame()
    if observe(image, vision, trace)[0] != "ad":
        raise Stop("关闭前广告页面已变化。")
    close = vision.match(image, "ad_close", (479, 1675, 603, 1890))
    if close is None:
        raise Stop("无法确认已有广告的关闭按钮。")
    phone.tap(center(close))
    trace.record("ad_dismissed")


def find_task(phone, vision, trace, *, allow_missing=False, page=None):
    listing = {"list", "task", "task_unavailable"}
    rewards = {"reward", "reward_final"}
    if page is None:
        image = phone.frame()
        state, _ = vision.identify(image)
    else:
        image, state, _ = page
    if state == "profile":
        tap_verified(phone, vision, trace, "profile")
        image, state, _ = wait_for_page(phone, vision, {"coins"})
    if state == "coins":
        tap_verified(phone, vision, trace, "coins")
        image, state, _ = wait_for_page(phone, vision, listing | rewards)
    saw_top = False
    for down in (False, True):
        still = 0
        for step in range(17):
            if state in rewards:
                trace.snapshot(phone, "list-late-reward", image=image)
                _, (image, state, _) = dismiss_reward(phone, vision, trace)
                if state not in listing:
                    image, state, _ = wait_for_page(phone, vision, listing)
            if state in {"task", "task_unavailable"}:
                return image
            if state != "list":
                raise Stop("请先打开领币页首页，或关闭已有弹窗后再运行。")
            if not down and vision.match(image, "list_top", (178, 700, 475, 1000)):
                saw_top = True
                break
            if down and saw_top and vision.match(image, "list_end", (410, 1770, 716, 2320)):
                if allow_missing:
                    return None
                break
            if step == 16:
                break
            previous = crop(image, (32, 705, 1045, 2320))
            phone.scroll(down)
            time.sleep(0.4)
            image = phone.frame()
            state, _ = vision.identify(image)
            current = crop(image, (32, 705, 1045, 2320))
            still = still + 1 if state == "list" and cv2.norm(previous, current, cv2.NORM_L1) / current.size < 0.35 else 0
            if still >= 2:
                if not down:
                    saw_top = True
                break
    if state in {"task", "task_unavailable"}:
        return image
    raise Stop(f"未找到可执行的“{vision.task.name}”。")


def tap_qqlive_return(phone, vision, trace):
    component = phone.foreground_component()
    if component[0] != "com.tencent.qqlive":
        return False
    try:
        image = phone.capture()
    except CaptureUnavailable as exc:
        trace.record("screenshot_unavailable", state="external-return", reason=str(exc))
        return False
    button = vision.match(image, "ad_external_return", (0, 1770, 406, 2290), 0.88)
    if not button or phone.foreground_component() != component:
        return False
    phone.adb_call("shell", "input", "tap", *map(str, center(button)))
    trace.snapshot(phone, "external-return", image=image, component=component)
    return True


def wait_for_landing_unlock(phone, trace, landing_apps, timeout):
    trace.record("app_lock_wait", seconds=timeout)
    print(f"广告落地页需要在手机上解锁，等待 {timeout} 秒。", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        package = phone.foreground()
        if package in landing_apps:
            trace.record("app_lock_unlocked", package=package)
            return package
        if package == MGAME:
            break
        time.sleep(0.5)
    return APP_LOCK


def browse_external(phone, vision, trace, *, seconds=None, unlock_timeout=0):
    landing_apps = LANDING_APPS | PROMO_EXTERNAL_APPS | GOODS_EXTERNAL_APPS
    seconds = vision.task.seconds if seconds is None else seconds
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        package = phone.foreground()
        if package in landing_apps or package == APP_LOCK:
            break
        if package and package != MGAME:
            # An app protected by the OS can appear briefly before its lock screen.
            time.sleep(3)
            if phone.foreground() == APP_LOCK:
                package = APP_LOCK
                break
            raise LandingUnconfirmed(f"广告跳转到未识别的应用 {package}。")
        time.sleep(0.4)
    else:
        raise LandingUnconfirmed("未确认落地应用，无法开始计时。")
    if package == APP_LOCK and unlock_timeout:
        package = wait_for_landing_unlock(phone, trace, landing_apps, unlock_timeout)
        unlock_timeout = 0
    started = time.monotonic()
    trace.record("landing", package=package)
    trace.snapshot(phone, "app-lock" if package == APP_LOCK else "external-landing")
    if package != APP_LOCK:
        until = started + seconds + 2
        last_allowed = time.monotonic()
        while time.monotonic() < until:
            foreground = phone.foreground()
            if foreground == APP_LOCK:
                if unlock_timeout:
                    foreground = wait_for_landing_unlock(phone, trace, landing_apps, unlock_timeout)
                    unlock_timeout = 0
                if foreground == APP_LOCK:
                    package = APP_LOCK
                    break
                package = foreground
                last_allowed = time.monotonic()
                until = last_allowed + seconds + 2
            if foreground == package:
                last_allowed = time.monotonic()
            elif foreground in landing_apps:
                trace.record("landing_changed", previous=package, package=foreground)
                package = foreground
                last_allowed = time.monotonic()
                until = last_allowed + seconds + 2
            elif foreground is not None or time.monotonic() - last_allowed > 1.5:
                raise LandingUnconfirmed(f"浏览期间前台应用变化：{foreground}。")
            time.sleep(0.35)
        if package != APP_LOCK and phone.foreground() != package:
            raise LandingUnconfirmed("计时结束时广告落地页不在前台。")
    if package == APP_LOCK:
        trace.record("app_lock_return")
    elif package == "com.tencent.qqlive" and tap_qqlive_return(phone, vision, trace):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if phone.foreground() == MGAME:
                return False
            time.sleep(0.35)
    phone.return_to_mgame()
    return package == APP_LOCK


def browse_one(phone, vision, trace, before, progress):
    label = f"-{vision.task.key}-{progress[0] + 1}"
    task_frame = tap_verified(phone, vision, trace, "task", label, progress)
    seconds = vision.task_wait_seconds(task_frame)
    wanted = {"ad", "promo_landing"}
    landing, state, _ = wait_for_page(phone, vision, wanted, timeout=18, trace=trace)
    if landing is not None:
        trace.snapshot(phone, state, image=landing)
    locked = False
    if state in {"promo_landing", "promo_travel", "promo_live", "promo_farm", "promo_attraction", "promo_rank", "promo_lowprice"}:
        browse_promo_page(phone, vision, trace, page=state, seconds=seconds)
    elif state == "promo_external":
        locked = browse_external(phone, vision, trace, seconds=seconds)
    elif state == "promo_fullscreen_ad":
        component = phone.foreground_component()
        if component[0] != MGAME:
            raise LandingUnconfirmed("点击前全屏广告已离开前台。")
        button = promo_fullscreen_button(phone.frame(), component[1], trace)
        if button == DOUYIN_OPEN_BUTTON:
            packages = phone.adb_call("shell", "pm", "list", "packages", DOUYIN_PACKAGE).decode().splitlines()
            if f"package:{DOUYIN_PACKAGE}" not in packages:
                raise LandingUnconfirmed("未找到已安装的抖音，无法打开广告落地应用。")
        if not button or phone.foreground_component() != component:
            raise LandingUnconfirmed("点击前无法确认全屏广告按钮。")
        phone.tap(center(button))
        trace.record("promo_fullscreen_tap", activity=component[1], point=center(button))
        seconds = {PROMO_PORTRAIT_AD: 15, PROMO_SIGMOB_AD: 6}.get(component[1], seconds)
        locked = browse_external(phone, vision, trace, seconds=seconds, unlock_timeout=25)
    else:
        tap_verified(phone, vision, trace, "ad", label)
        # The ad dialog can independently require up to ten seconds.
        seconds = max(seconds, 10) if vision.task.key == "promo" else seconds
        locked = browse_external(phone, vision, trace, seconds=seconds)
    wanted = {"reward", "reward_final"}
    if locked:
        wanted.add("ad")
    after, state, _ = wait_for_page(
        phone, vision, wanted, timeout=12, trace=trace,
        settling=True,
    )
    if state == "ad":
        trace.snapshot(phone, "blocked-ad", image=after)
        dismiss_ad(phone, vision, trace)
        wait_for_page(phone, vision, {"task", "task_unavailable", "list"})
        trace.record("blocked_ad_retry", task=vision.task.key)
        return None
    trace.snapshot(phone, state, image=after)
    trace.evidence(after, state, None, f"reward{label}.png")
    before_count, after_count = crop(before, CHANCES), crop(after, CHANCES)
    proof = np.concatenate((before_count, after_count), axis=1)
    save_image(trace.directory / f"chances-before-after{label}.png", proof)
    trace.record("confirmed", task=vision.task.key, reward_chances=vision.task.reward)
    print(f"{vision.task.name}本轮结算 +{vision.task.reward} 次。", flush=True)
    return state


def close_promo_live(phone, vision, trace):
    for _ in range(4):
        if phone.foreground_component() != [MGAME, PROMO_LIVE]:
            return
        image = phone.frame()
        follow = vision.match(image, "live_follow", (400, 2155, 686, 2330))
        if follow:
            close = vision.match(image, "live_follow_close", (970, 1750, 1044, 1840))
        else:
            close = vision.match(image, "live_exit", (962, 144, 1046, 219), 0.78)
            if close is None:
                close = vision.match(image, "live_exit", (962, 144, 1046, 219), 0.94, white_mask=True)
        if close is None:
            break
        if phone.foreground_component() != [MGAME, PROMO_LIVE]:
            return
        phone.tap(center(close))
        trace.record("live_close_tapped", target="follow_popup" if follow else "room", point=center(close))
        time.sleep(0.75)
    if phone.foreground_component() != [MGAME, PROMO_LIVE]:
        return
    raise LandingUnconfirmed("无法确认直播页关闭按钮。")


def browse_promo_page(phone, vision, trace, *, page="promo_landing", seconds=None):
    duration = (vision.task.seconds if seconds is None else seconds) + 2
    trace.record("landing", page=page, duration=duration)
    activity = {
        "promo_travel": PROMO_WEBVIEW, "promo_live": PROMO_LIVE, "promo_farm": GAME_ACTIVITY,
        "promo_attraction": PROMO_ATTRACTION,
        "promo_lowprice": PROMO_WEBVIEW,
    }.get(page)
    if page == "promo_rank":
        activity = phone.foreground_component()[1]
    deadline = time.monotonic() + duration
    while True:
        if activity:
            on_page = phone.foreground_component() == [MGAME, activity]
        else:
            component = phone.foreground_component()
            on_page = component in ([MGAME, PROMO_ACTIVITY], [MGAME, PROMO_SEARCH]) or (
                component == [MGAME, PROMO_GROUP]
                and vision.match(phone.frame(), "promo_group_home", (80, 115, 550, 210))
            ) or vision.identify(phone.frame())[0] == "promo_landing"
        if not on_page:
            raise LandingUnconfirmed("优惠活动页在计时中发生变化。")
        if time.monotonic() >= deadline:
            if page == "promo_travel" and not is_travel_promo(phone.frame(), trace):
                raise LandingUnconfirmed("计时结束时无法确认境外酒店活动页。")
            if page == "promo_rank" and not is_ranked_promo(phone.frame(), trace):
                raise LandingUnconfirmed("计时结束时无法确认活动榜单。")
            if page == "promo_live":
                close_promo_live(phone, vision, trace)
            elif page == "promo_farm":
                close = vision.match(phone.frame(), "promo_farm_exit", (918, 61, 1034, 142))
                if close is None:
                    raise Stop("未确认养鸡活动的退出按钮。")
                phone.tap(center(close))
                trace.record("promo_farm_closed", point=center(close))
            else:
                phone.back()
            return
        time.sleep(0.7)


def dismiss_reward(phone, vision, trace):
    image = phone.frame()
    state, box = vision.identify(image)
    if state == "reward":
        close = vision.match(image, "reward_close", (110, 1490, 512, 1640), 0.87)
    elif state == "reward_final":
        close = vision.match(image, "reward_final", (390, 1525, 690, 1670))
    else:
        return None, (image, state, box)
    if close is None:
        raise Stop("无法确认奖励弹窗的关闭按钮。")
    phone.tap(center(close))
    trace.record("reward_dismissed")
    page = wait_for_page(phone, vision, {"coins", "list", "task", "task_unavailable"})
    return state, page


def recover_ad_round(phone, vision, trace, failure):
    trace.record("ad_recovery", reason=str(failure))
    trace.snapshot(phone, "ad-recovery")
    if phone.foreground() != MGAME:
        phone.return_to_mgame()
    else:
        image = phone.frame()
        state, _ = observe(image, vision, trace)
        if state == "unknown" and not close_ad_webview(phone, vision, image, trace):
            phone.back()
    targets = {"coins", "profile", "list", "task", "task_unavailable", "ad", "reward", "reward_final"}
    try:
        image, state, _ = wait_for_page(phone, vision, targets, timeout=6, trace=trace, settling=True)
    except LandingUnconfirmed:
        phone.recover_mgame_main(vision, trace)
        trace.snapshot(phone, "ad-recovered-main")
        return None
    trace.snapshot(phone, "ad-recovered-" + state, image=image)
    if state == "ad":
        dismiss_ad(phone, vision, trace)
        image, state, _ = wait_for_page(phone, vision, targets - {"ad"}, timeout=6)
        if state in {"reward", "reward_final"}:
            trace.snapshot(phone, state, image=image)
    return state if state in {"reward", "reward_final"} else None


def finish_task(phone, vision, trace, *, allow_missing=False):
    dismissed, page = dismiss_reward(phone, vision, trace)
    if dismissed == "reward_final" and sum(task.badge == vision.task.badge for task in TASKS) == 1:
        trace.record("task_complete", task=vision.task.key, evidence="final_reward_dialog")
        print("Mgame已显示最后一轮结算，剩余 0 轮。")
        return
    frame = find_task(phone, vision, trace, allow_missing=allow_missing, page=page)
    if frame is None:
        trace.record("task_not_listed", task=vision.task.key)
        print(f"全列表未显示“{vision.task.name}”，跳过。", flush=True)
        return
    progress = read_progress(frame, vision, trace, f"{vision.task.key}-start")
    done, total = progress
    print(f"{vision.task.name} 当前进度 {done}/{total}，剩余 {total - done} 轮。", flush=True)
    for expected in range(done + 1, total + 1):
        last_round = expected == total
        before_count = timed_count(frame, trace, f"{vision.task.key}-{expected}-before") if last_round else None
        for attempt in range(4):
            try:
                reward = browse_one(phone, vision, trace, frame, progress)
            except (LandingUnconfirmed, ForegroundChanged, CaptureUnavailable) as exc:
                reward = recover_ad_round(phone, vision, trace, exc)
            if reward is not None:
                break
            frame = find_task(phone, vision, trace, allow_missing=last_round)
            if frame is None:
                break
            refreshed = read_progress(frame, vision, trace, f"{vision.task.key}-retry-{expected}")
            if refreshed == (expected, total):
                progress = refreshed
                reward = "progress_confirmed"
                break
            if refreshed != progress:
                raise Stop(f"广告关闭后进度异常变化：{refreshed[0]}/{refreshed[1]}。")
            if attempt == 1 and vision.task.key == "promo":
                trace.record("promo_round_restarting", completed=refreshed[0], total=total)
                phone.restart_mgame(vision, trace)
                frame = find_task(phone, vision, trace, allow_missing=last_round)
                if frame is None:
                    break
                refreshed = read_progress(frame, vision, trace, f"{vision.task.key}-restart-{expected}")
                if refreshed == (expected, total):
                    progress = refreshed
                    reward = "progress_confirmed"
                    break
                if refreshed != progress:
                    raise Stop(f"重启后进度异常变化：{refreshed[0]}/{refreshed[1]}。")
        else:
            raise RoundUnsettled("本轮尝试四次后仍未核对到结算。")
        if reward == "progress_confirmed":
            print(f"恢复后已核对进度 {expected}/{total}。", flush=True)
            continue
        if reward is not None:
            dismissed, page = dismiss_reward(phone, vision, trace)
            if reward == "reward_final" or dismissed == "reward_final":
                if not last_round:
                    raise Stop(f"Mgame已结束本组任务，上一轮进度为 {progress[0]}/{total}。")
                print(f"最后一轮已结算，任务达到 {total}/{total}。", flush=True)
                break
            frame = find_task(phone, vision, trace, allow_missing=last_round, page=page)
        if frame is None:
            after = phone.frame()
            after_count = timed_count(after, trace, f"{vision.task.key}-{expected}-after")
            if before_count is None or after_count != before_count + vision.task.reward:
                raise Stop("最后一轮任务移出列表，但未核对到对应的领币次数增加。")
            trace.snapshot(phone, "final-row-removed", image=after)
            trace.record("final_row_removed", task=vision.task.key, before=before_count, after=after_count)
            break
        progress = read_progress(frame, vision, trace, f"{vision.task.key}-after-{expected}")
        if progress != (expected, total):
            raise Stop(f"预期进度 {expected}/{total}，实际 {progress[0]}/{progress[1]}。")
        print(f"已核对进度 {expected}/{total}。", flush=True)
    trace.record("task_complete", task=vision.task.key, completed=total, total=total)
    print(f"{vision.task.name}已完成 {total}/{total}。")


def rule_positions(vision, image, name, region):
    template = vision.templates[name]
    gray = cv2.cvtColor(crop(image, region), cv2.COLOR_BGR2GRAY)
    scores = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    rows = []
    for y, score in enumerate(scores.max(axis=1)):
        if score < 0.94:
            continue
        if rows and y - rows[-1][0] < 70:
            if score > rows[-1][1]:
                rows[-1] = (y, score)
        else:
            rows.append((y, score))
    return [y + region[1] for y, _ in rows]


def game60_rows(vision, image):
    for y in rule_positions(vision, image, "game60_rule", (184, 735, 455, 2345)):
        if y < 780:
            continue
        buttons = (853, y - 51, 1030, min(2349, y + 34))
        claim = vision.match(image, "game60_claim", buttons)
        go = vision.match(image, "go", buttons)
        if bool(claim) != bool(go):
            yield y, ("claim" if claim else "go"), (claim or go)


TIMED_SECONDS = {"browse10": 10, "play20": 20, "listen30": 30, "watch60": 60}
TIMED_LABELS = {
    "browse10": "浏览 10 秒得闪电奖励",
    "play20": "玩 20 秒得闪电奖励",
    "listen30": "听小说 30 秒",
    "watch60": "看短剧 60 秒",
}
TIMED_PLAYERS = {
    "listen30": ("com.meituan.android.novel.library.page.reader.ReaderActivity",),
    "watch60": ("com.meituan.android.novel.library.page.video.stream.NovelVideoActivity",
                MGAME + ".msv.page.activity.MSVPageActivity"),
}
NOVEL_LIBRARY = "com.meituan.android.mrnx.container.MRNXStandardActivity"


def browse10_rows(vision, image, rule="browse10"):
    for y in rule_positions(vision, image, f"{rule}_rule", (182, 760, 420, 2345)):
        if y < 795 or y > 2310:
            continue
        if rule == "browse10" and vision.match(image, TASKS[2].title, (175, y - 84, 565, y - 4)):
            continue
        buttons = (840, y - 67, 1042, y + 34)
        claim = vision.match(image, "game60_claim", buttons)
        go = vision.match(image, "go", buttons)
        if bool(claim) != bool(go):
            yield y, ("claim" if claim else "go"), (claim or go)


def browse10_reward(image, y, vision, trace, rule="browse10"):
    left = 415 if rule == "browse10" else 382
    region = (left, y - 4, left + 70, y + 47)
    for reward, template in ((2, "browse10_plus_two"), (1, "timed_plus_one")):
        if template in vision.templates and vision.match(image, template, region, 0.96):
            return reward
    digits = crop(image, region)
    red = (digits[:, :, 2] > 188) & (digits[:, :, 1] < 135)
    binary = np.where(red, 0, 255).astype(np.uint8)
    points = cv2.findNonZero(255 - binary)
    if points is not None:
        x, top, width, height = cv2.boundingRect(points)
        amount = cv2.resize(binary[top:top + height, x:x + width], (60, 48), interpolation=cv2.INTER_NEAREST)
        three = vision.templates["timed_amount_three"]
        if cv2.matchTemplate(amount, three, cv2.TM_CCOEFF_NORMED)[0, 0] >= 0.92:
            return 3
    enlarged = cv2.resize(binary, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    padded = cv2.copyMakeBorder(enlarged, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    value = re.sub(r"\s+", "", read_ocr(trace, padded, "browse10-reward.png"))
    if not re.fullmatch(r"\+?[1-9]", value):
        raise Stop(f"无法核对小字中的浏览奖励：{value!r}。")
    return int(value.lstrip("+"))


def browse10_key(image, y):
    return crop(image, (43, y - 73, 620, y - 8))


def matches_browse10(image, y, icon):
    return cv2.matchTemplate(browse10_key(image, y), icon, cv2.TM_CCOEFF_NORMED)[0, 0] >= 0.94


def game_count(image, trace, tag):
    digits = crop(image, (90, 401, 160, 454))
    gray = cv2.cvtColor(digits, cv2.COLOR_BGR2GRAY)
    _, light = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    caption = cv2.cvtColor(crop(image, (610, 592, 731, 645)), cv2.COLOR_BGR2GRAY)
    _, caption = cv2.threshold(caption, 140, 255, cv2.THRESH_BINARY)
    line = np.concatenate((caption, np.full((53, 12), 255, np.uint8), light), axis=1)
    enlarged = cv2.resize(line, None, fx=2, fy=2)
    padded = cv2.copyMakeBorder(enlarged, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
    for attempt in range(3):
        value = read_ocr(trace, padded, f"game60-count-{tag}-{attempt}.png")
        match = re.search(r"(?<!\d)(\d{1,4})\s*$", value)
        if match:
            return int(match[1])
        padded = cv2.copyMakeBorder(padded, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=255)
    raise CountUnreadable("无法读取任务列表的领币次数。")


def timed_count(image, trace, tag, *, alternate=False):
    variants = [((85, 399, 166, 454), 2, 30, "-alternate")]
    if not alternate:
        variants.insert(0, ((87, 402, 160, 451), 3, 20, ""))
    for region, scale, margin, suffix in variants:
        gray = cv2.cvtColor(crop(image, region), cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY_INV)
        scaled = cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
        padded = cv2.copyMakeBorder(scaled, margin, margin, margin, margin, cv2.BORDER_CONSTANT, value=255)
        value = read_ocr(trace, padded, f"timed-count-{tag}{suffix}.png")
        if re.fullmatch(r"\d{1,4}", value):
            return int(value)
    return game_count(image, trace, tag)


def confirm_reward_count(capture, trace, tag, before, reward, timeout):
    expected = before + reward
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        image = capture()
        try:
            after = timed_count(image, trace, f"{tag}-{attempt}")
        except CountUnreadable:
            after = None
        if after is not None and after not in {before, expected}:
            try:
                alternate = timed_count(image, trace, f"{tag}-{attempt}-recheck", alternate=True)
            except CountUnreadable:
                alternate = None
            trace.record("reward_count_ocr_recheck", check=tag, attempt=attempt, observed=after, alternate=alternate)
            if alternate == expected:
                return image, alternate
        if after == expected:
            return image, after
        trace.record("reward_count_retry", check=tag, attempt=attempt, expected=expected, observed=after)
        if time.monotonic() >= deadline:
            raise Stop(f"未确认领奖 +{reward}，原次数 {before}，最后读数 {after if after is not None else '无法识别'}。")
        time.sleep(0.5)


def game60_panel(phone, vision, trace):
    listing = {"list", "task", "task_unavailable"}
    rewards = {"reward", "reward_final"}
    entrance = {"profile", "coins"} | listing
    image, state, _ = wait_for_page(phone, vision, entrance | rewards, timeout=15)
    if state in rewards:
        _, (image, state, _) = dismiss_reward(phone, vision, trace)
        if state not in entrance:
            image, state, _ = wait_for_page(phone, vision, entrance)
    if state == "profile":
        tap_verified(phone, vision, trace, "profile")
        _, state, _ = wait_for_page(phone, vision, {"coins"})
    if state == "coins":
        tap_verified(phone, vision, trace, "coins")
        image, state, _ = wait_for_page(phone, vision, listing | rewards)
        if state in rewards:
            _, (image, state, _) = dismiss_reward(phone, vision, trace)
            if state not in listing:
                image, _, _ = wait_for_page(phone, vision, listing)
    trace.record("game60_panel_open")
    return image


def scan_game60(phone, vision, image, state, title=None, *, rule=None, exclude=(), exclude_go=()):
    for down in (False, True):
        still = 0
        for _ in range(20):
            if state in {"claim", "either"} or down:
                rows = browse10_rows(vision, image, rule) if rule else game60_rows(vision, image)
                if rule:
                    rows = (row for row in rows if (title is None or matches_browse10(image, row[0], title))
                            and not any(matches_browse10(image, row[0], icon) for icon in exclude)
                            and (row[1] != "go" or not any(matches_browse10(image, row[0], icon) for icon in exclude_go)))
                else:
                    rows = (row for row in rows if title is None or matches_game60_title(image, row, title))
                if state == "either":
                    found = min(rows, key=lambda row: row[1] != "claim", default=None)
                else:
                    found = next((row for row in rows if row[1] == state), None)
                if found:
                    return image, found
            if down and vision.match(image, "list_end", (410, 1770, 716, 2320)):
                return image, None
            phone.scroll(down)
            time.sleep(0.4)
            next_image = phone.frame()
            if not vision.match(next_image, "panel", (270, 555, 800, 688)):
                raise Stop("扫描期间已离开加次数任务面板。")
            old = crop(image, (32, 705, 1045, 2320))
            new = crop(next_image, (32, 705, 1045, 2320))
            still = still + 1 if cv2.norm(old, new, cv2.NORM_L1) / new.size < 0.35 else 0
            image = next_image
            if still >= 2:
                if down:
                    raise Stop("列表停住，但未看到列表底部标志。")
                break
        else:
            raise Stop("扫描达到长度上限。")
    return image, None


def game60_ad_close(vision, image):
    # Match the foreground ad's close control, excluding the game's underlying X.
    return vision.match(image, "game60_ad_close", (786, 610, 948, 727), 0.92) or vision.match(
        image, "ad_close", (786, 610, 948, 727), 0.94
    )


def leave_game60_ad(phone, vision, trace):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        component = phone.foreground_component()
        if component == [MGAME, GAME_ACTIVITY]:
            return
        if component == [MGAME, GAME_AD_COMPACT]:
            raise Stop("当前插屏的“关闭”实测会打开广告落地页，请手动退出插屏。")
        if component != [MGAME, GAME_AD]:
            raise Stop("广告画面已变化，未发送关闭点击。")
        image = phone.frame()
        button = game60_ad_close(vision, image)
        if button:
            if phone.foreground_component() != component:
                raise Stop("点击前广告已切换，未发送关闭点击。")
            save_image(trace.directory / "game60-ad-close.png", crop(image, button))
            phone.tap(center(button))
            trace.record("game60_ad_close_tap", point=center(button))
            break
        time.sleep(0.7)
    else:
        raise Stop("计时结束，未识别到插屏的关闭按钮或 X。请手动关闭。")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        component = phone.foreground_component()
        if component == [MGAME, GAME_ACTIVITY]:
            trace.record("game60_ad_closed")
            return
        if component[0] not in (None, MGAME):
            raise Stop(f"关闭按钮跳转到 {component[0]}。请手动返回游戏。")
        time.sleep(0.5)
    raise Stop("点击关闭后未回到游戏。请手动返回。")


def play_game60(phone, vision, trace, seconds=GAME_WAIT_SECONDS, *, foreground=False):
    activities = ([MGAME, GAME_ACTIVITY],) if foreground else GAME_COMPONENTS
    deadline = time.monotonic() + 45
    while phone.foreground_component() not in activities:
        if time.monotonic() >= deadline:
            raise Stop("未能确认游戏打开。")
        time.sleep(0.5)

    trace.snapshot(phone, "game-open")
    if foreground:
        trace.record("game60_foreground_started", seconds=seconds)
        print(f"游戏前台补跑，等待 {seconds} 秒。", flush=True)
        deadline = time.monotonic() + seconds
        while True:
            if phone.foreground_component() not in activities:
                raise LandingUnconfirmed("游戏前台补跑期间页面切换，未确认计时。")
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)
        trace.record("game60_timer_elapsed", seconds=seconds, mode="foreground")
    else:
        launcher = phone.home()
        deadline = time.monotonic() + 5
        while phone.foreground() != launcher:
            if time.monotonic() >= deadline:
                raise Stop("按 Home 后未确认回到手机桌面。")
            time.sleep(0.4)
        trace.record("game60_background_started", seconds=seconds)
        deadline = time.monotonic() + seconds
        trace.snapshot(phone, "game-desktop")
        print(f"已回到手机桌面，等待 {seconds} 秒后切回Mgame。", flush=True)
        time.sleep(max(0, deadline - time.monotonic()))
        trace.record("game60_timer_elapsed", seconds=seconds, mode="background")
        phone.return_to_mgame()
    try:
        image = return_game60_panel(phone, vision, trace)
    except DeviceDisconnected:
        raise
    except Stop as exc:
        trace.record("game60_recovery", reason=str(exc))
        trace.snapshot(phone, "game-restore")
        print("游戏页未能直接返回，重新进入「我的 → 领币页 → 加次数」。", flush=True)
        phone.recover_mgame_main(vision, trace)
        image = game60_panel(phone, vision, trace)
    trace.snapshot(phone, "game-return", image=image)
    return image


def return_game60_panel(phone, vision, trace):
    listing = {"list", "task", "task_unavailable"}
    deadline = time.monotonic() + 18
    back_sent = False
    while time.monotonic() < deadline:
        component = phone.foreground_component()
        if component[0] == MGAME:
            try:
                image = phone.frame()
            except (CaptureUnavailable, ForegroundChanged):
                time.sleep(0.4)
                continue
            state, _ = vision.identify(image)
            if state in listing:
                trace.record("game60_return")
                return image
            if state in {"profile", "coins"}:
                return game60_panel(phone, vision, trace)
            if component == [MGAME, GAME_AD_COMPACT]:
                raise Stop("切回后仍显示飞猪插屏。")
            if (component == [MGAME, GAME_ACTIVITY] and
                vision.match(image, "game60_update_prompt", (140, 1020, 950, 1220), 0.92)):
                raise Stop("游戏显示更新提示，改从Mgame主界面恢复。")
            if not back_sent and component in GAME_COMPONENTS and phone.foreground_component() == component:
                phone.back()
                trace.record("game60_back", activity=component[1])
                back_sent = True
                deadline = min(deadline, time.monotonic() + 6)
        time.sleep(0.5)

    # Some game containers and interstitials ignore Android Back.
    component = phone.foreground_component()
    if component in GAME60_AD_COMPONENTS:
        leave_game60_ad(phone, vision, trace)
    if phone.foreground_component() != [MGAME, GAME_ACTIVITY]:
        raise Stop("切回Mgame后未能返回游戏任务列表。")
    image = phone.frame()
    button = (vision.match(image, "game60_exit", (925, 25, 1079, 210), 0.88)
              or vision.match(image, "game60_exit_capsule", (925, 25, 1079, 175), 0.90))
    if not button:
        raise Stop("未识别到小游戏的退出按钮。")
    if phone.foreground_component() != [MGAME, GAME_ACTIVITY]:
        return game60_panel(phone, vision, trace)
    phone.tap(center(button))
    trace.record("game60_exit_tap", point=center(button))
    trace.snapshot(phone, "game-exit-control", image=image, component=[MGAME, GAME_ACTIVITY])
    image, _, _ = wait_for_page(phone, vision, listing, timeout=8)
    trace.record("game60_return")
    return image


def game60_title(image, y):
    return crop(image, (187, y - 68, 620, y - 10))


def matches_game60_title(image, row, title):
    return cv2.matchTemplate(game60_title(image, row[0]), title, cv2.TM_CCOEFF_NORMED)[0, 0] >= 0.91


def same_game60(image, candidates, title, action):
    matching = [
        row for row in candidates if row[1] == action
        and matches_game60_title(image, row, title)
    ]
    if len(matching) != 1:
        raise Stop("无法在新截图中确认同一游戏任务行。")
    return matching[0]


def finish_games(phone, vision, trace, *, foreground=False):
    seconds = GAME_FOREGROUND_RETRY_SECONDS if foreground else GAME_WAIT_SECONDS
    component = phone.foreground_component()
    if component in GAME_COMPONENTS:
        image = play_game60(phone, vision, trace, seconds=seconds, foreground=foreground)
    else:
        if component[0] != MGAME:
            phone.recover_mgame_main(vision, trace)
        image = game60_panel(phone, vision, trace)
    for index in range(20):
        image, row = scan_game60(phone, vision, image, "either")
        if row is None:
            print("已扫到列表底部，当前没有剩余的“玩60秒得 +3”游戏。", flush=True)
            trace.record("game60_no_remaining")
            return
        if row[1] == "go":
            y, action, box = row
            selected = game60_title(image, y).copy()
            save_image(trace.directory / f"game60-title-{index}.png", selected)
            fresh = phone.frame()
            _, _, box = same_game60(fresh, game60_rows(vision, fresh), selected, "go")
            phone.tap(center(box))
            image = play_game60(phone, vision, trace, seconds=seconds, foreground=foreground)
            image, row = scan_game60(phone, vision, image, "claim", title=selected)
            if row is None and not foreground:
                image, row = scan_game60(phone, vision, image, "either", title=selected)
                if row is not None and row[1] == "go":
                    trace.record("game60_foreground_retry")
                    fresh = phone.frame()
                    _, _, box = same_game60(fresh, game60_rows(vision, fresh), selected, "go")
                    phone.tap(center(box))
                    image = play_game60(phone, vision, trace, seconds=GAME_FOREGROUND_RETRY_SECONDS, foreground=True)
                    image, row = scan_game60(phone, vision, image, "claim", title=selected)
            if row is None:
                raise Stop("停留后没有看到“可领奖”，未确认游戏任务完成。")
        else:
            selected = game60_title(image, row[0]).copy()
        fresh = phone.frame()
        _, _, box = same_game60(fresh, game60_rows(vision, fresh), selected, "claim")
        before = timed_count(fresh, trace, f"game60-{index}-before")
        phone.tap(center(box))
        time.sleep(2)
        image, after = confirm_reward_count(phone.frame, trace, f"game60-{index}-after", before, 3, timeout=8)
        trace.record("game60_confirmed", before=before, after=after)
        trace.snapshot(phone, "game-reward", image=image)
        print(f"游戏奖励已到账 +3（{before} → {after}）。", flush=True)
    raise Stop("达到单次执行上限。")


def is_store_detail(image, trace):
    # Store headers wrap differently, moving the same navigation labels vertically.
    tabs = crop(image, (24, 1200, 710, 1500))
    gray = cv2.cvtColor(tabs, cv2.COLOR_BGR2GRAY)
    if np.mean(gray > 185) < 0.65:
        return False
    labels = re.sub(r"\s+", "", read_ocr(trace, tabs, "browse-store-tabs.png"))
    return all(label in labels for label in ("团购", "评价", "推荐"))


def close_share_dialog(phone, vision, trace, image):
    if not vision.match(image, "share_dialog", (370, 845, 724, 968), 0.88):
        return False
    close = vision.match(image, "share_close", (858, 825, 959, 930), 0.88)
    if close is None:
        raise Stop("检测到分享弹窗，未确认关闭按钮。")
    trace.snapshot(phone, "share-dialog", image=image)
    phone.tap(center(close))
    return True


def browse10_landing(phone, vision, trace):
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        component = phone.foreground_component()
        if component[0] == MGAME and component[1] in BROWSE_VERIFICATION_ACTIVITIES:
            trace.snapshot(phone, "verification-required", component=component)
            trace.record("verification_required", activity=component[1])
            raise VerificationRequired("浏览任务要求在手机上完成验证，待人工处理后再继续。")
        if component[0] == MGAME:
            try:
                image = phone.frame()
            except (ForegroundChanged, CaptureUnavailable):
                time.sleep(0.5)
                continue
            if close_share_dialog(phone, vision, trace, image):
                raise LandingUnconfirmed("浏览按钮点击后出现分享浮层，已关闭。")
            monthly = vision.match(image, "browse10_month", (52, 505, 540, 706))
            review = component == [MGAME, PROMO_ACTIVITY] and vision.match(
                image, "browse10_review", (380, 122, 710, 216)
            )
            if (monthly or review or vision.identify(image)[0] == "promo_landing"
                or (component == [MGAME, PROMO_GROUP]
                    and vision.match(image, "promo_group_home", (80, 115, 550, 210)))
                or (component == [MGAME, "com.meituan.android.mrn.container.MRNBaseActivity"]
                    and is_store_detail(image, trace))
                or component == [MGAME, MGAME + ".msv.page.activity.MSVPageActivity"]
                or (component == [MGAME, "com.meituan.msc.modules.container.MSCActivity"]
                    and vision.match(image, "browse10_merchant", (398, 120, 690, 212)))
                or (component == [MGAME, PROMO_WEBVIEW]
                    and vision.match(image, "browse10_wallet", (79, 120, 416, 212)))
                or vision.match(image, "browse10_group", (80, 103, 287, 195))):
                trace.record("browse10_landing", activity=component[1], seconds=12)
                deadline = time.monotonic() + 12
                trace.snapshot(phone, "browse-landing", image=image, component=component)
                time.sleep(max(0, deadline - time.monotonic()))
                if phone.foreground_component() != component:
                    raise Stop("浏览过程中页面已切换，未确认本轮。")
                if monthly:
                    leave_browse10_month(phone, vision, trace)
                else:
                    phone.back()
                    if review:
                        confirm_browse10_review_exit(phone, vision, trace)
                return game60_panel(phone, vision, trace)
        time.sleep(0.5)
    raise LandingUnconfirmed("未识别浏览任务打开的页面。")


def leave_browse10_month(phone, vision, trace):
    image = phone.frame()
    close = vision.match(image, "browse10_month_close", (965, 330, 1049, 418))
    if not (close and vision.match(image, "browse10_month", (52, 505, 540, 706))):
        raise Stop("未确认月付活动浮层的关闭按钮。")
    phone.tap(center(close))
    trace.record("browse10_month_closed")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if phone.foreground_component() == [MGAME, PROMO_WEBVIEW]:
            phone.back()
            return
        time.sleep(0.5)
    raise Stop("关闭活动浮层后未确认返回入口。")


def browse10_review_exit_button(image, vision, trace):
    prompt_region = (330, 1092, 734, 1215)
    leave_region = (190, 1380, 495, 1480)
    dialog = vision.match(image, "browse10_review_prompt", prompt_region)
    button = vision.match(image, "browse10_review_leave", leave_region)
    if dialog and button:
        return button
    if not vision.match(image, "browse10_review", (380, 122, 710, 216)):
        return None
    title = read_ocr(trace, crop(image, prompt_region), "browse10-review-exit-title.png", reuse=True)
    title = re.sub(r"\s+", "", title)
    if title.rstrip("?？") != "确认要离开吗":
        return None
    label = read_ocr(trace, crop(image, leave_region), "browse10-review-exit-button.png", reuse=True)
    return leave_region if re.sub(r"\s+", "", label) == "确认离开" else None


def is_browse10_review_home(image, vision, trace):
    if vision.match(image, "browse10_review_home", (350, 1470, 730, 1720)):
        return True
    if not vision.match(image, "browse10_review", (380, 122, 710, 216)):
        return False
    text = read_ocr(trace, crop(image, (388, 1498, 704, 1589)), "browse10-review-home.png", reuse=True)
    return re.sub(r"\s+", "", text) == "开始评审"


def confirm_browse10_review_exit(phone, vision, trace, *, resume_image=None):
    activity = [MGAME, PROMO_ACTIVITY]
    if resume_image is not None:
        if phone.foreground_component() != activity:
            return
        if is_browse10_review_home(resume_image, vision, trace):
            if phone.foreground_component() == activity:
                phone.back()
            return
        if not browse10_review_exit_button(resume_image, vision, trace) and phone.foreground_component() == activity:
            phone.back()

    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if phone.foreground_component() != activity:
            return
        image = phone.frame()
        button = browse10_review_exit_button(image, vision, trace)
        if button and phone.foreground_component() == activity:
            phone.tap(center(button))
            trace.record("browse10_review_exit_confirmed")
            break
        time.sleep(0.5)
    else:
        raise LandingUnconfirmed("未确认评审团的离开按钮。")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if phone.foreground_component() != activity:
            return
        if is_browse10_review_home(phone.frame(), vision, trace):
            if phone.foreground_component() == activity:
                phone.back()
            return
        time.sleep(0.5)
    raise LandingUnconfirmed("未确认评审团首页已加载。")


def timed_media(phone, vision, trace, rule):
    listening = rule == "listen30"
    players = [[MGAME, activity] for activity in TIMED_PLAYERS[rule]]
    deadline = time.monotonic() + 18
    while phone.foreground_component() not in players:
        if time.monotonic() >= deadline:
            raise LandingUnconfirmed("未识别计时任务的播放页面。")
        if listening and close_novel_ad(phone, vision, trace):
            continue
        time.sleep(0.5)
    if listening:
        start_novel_audio(phone, vision, trace)
    seconds = TIMED_SECONDS[rule] + 2
    trace.record("timed_media_started", rule=rule, seconds=seconds)
    deadline = time.monotonic() + seconds
    trace.snapshot(phone, rule + "-playing")
    print(f"已打开播放页，前台停留 {seconds} 秒。", flush=True)
    while True:
        component = phone.foreground_component()
        if component not in players:
            trace.snapshot(phone, rule + "-interrupted", component=component)
            trace.record("timed_media_interrupted", rule=rule, component=component)
            raise LandingUnconfirmed("计时期间播放页离开前台，需重新确认本阶段。")
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    trace.record("timed_media_completed", rule=rule, seconds=seconds)
    phone.back()
    if listening:
        leave_novel_audio(phone, vision)
    leave_drama(phone)
    return game60_panel(phone, vision, trace)


def leave_drama(phone):
    for _ in range(3):
        time.sleep(1)
        package, activity = phone.foreground_component()
        if package != MGAME or activity not in TIMED_PLAYERS["watch60"]:
            return
        phone.back()


def close_novel_ad(phone, vision, trace):
    component = phone.foreground_component()
    if component != [MGAME, MGAME + ".msv.page.activity.MSVPageActivity"]:
        return False
    image = phone.frame()
    close = vision.match(image, "novel_ad_close", (956, 155, 1053, 268), 0.90)
    banner = vision.match(image, "novel_ad_banner", (70, 160, 570, 270), 0.88)
    if not (close and banner):
        return False
    if phone.foreground_component() != component:
        return False
    phone.tap(center(close))
    trace.snapshot(phone, "novel-ad-close-attempt", image=image, component=component)
    time.sleep(0.6)
    if phone.foreground_component() == component:
        after = phone.frame()
        if vision.match(after, "novel_ad_banner", (70, 160, 570, 270), 0.88):
            phone.back()
            trace.snapshot(phone, "novel-ad-back", image=after, component=component)
            time.sleep(0.6)
    return True


def start_novel_audio(phone, vision, trace):
    pause_region = (156, 1905, 243, 2027)
    audio_region = (798, 1880, 1045, 1982)
    ui_deadline = time.monotonic() + 16
    while time.monotonic() < ui_deadline:
        if close_novel_ad(phone, vision, trace):
            continue
        if phone.foreground_component() != [MGAME, TIMED_PLAYERS["listen30"][0]]:
            time.sleep(0.5)
            continue
        image = phone.frame()
        popup = vision.match(image, "listen30_popup", (133, 475, 845, 700))
        if popup:
            close = vision.match(image, "listen30_popup_close", (874, popup[1], 942, popup[3] + 20))
            if close is None:
                raise LandingUnconfirmed("已识别听书签到浮层，未确认关闭按钮。")
            if phone.foreground_component() != [MGAME, TIMED_PLAYERS["listen30"][0]]:
                raise LandingUnconfirmed("关闭听书签到浮层前页面已切换。")
            trace.snapshot(phone, "novel-popup-close-attempt", image=image)
            phone.tap(center(close))
            trace.record("novel_popup_close_tapped", point=center(close))
        elif vision.match(image, "listen30_pause", pause_region):
            trace.record("novel_playback_confirmed")
            return
        else:
            start = vision.match(image, "listen30_page_audio", audio_region)
            if start:
                phone.tap(center(start))
                break
            # Find an unobstructed paper patch near the center of the reader.
            for x, y in ((531, 1164), (531, 1040), (531, 1280), (531, 1400)):
                pixels = crop(image, (x - 18, y - 18, x + 18, y + 18)).astype(np.int16)
                if np.mean(np.max(np.abs(pixels - [196, 215, 220]), axis=2) < 22) >= 0.80:
                    phone.tap((x, y))
                    break
        time.sleep(0.75)
    else:
        raise LandingUnconfirmed("未确认听书控件，无法开始播放。")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if vision.match(phone.frame(), "listen30_pause", pause_region):
            trace.record("novel_playback_confirmed")
            return
        time.sleep(0.5)
    raise LandingUnconfirmed("未确认小说开始播放。")


def leave_novel_audio(phone, vision):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        component = phone.foreground_component()
        if component == [MGAME, TIMED_PLAYERS["listen30"][0]] or component[0] is None:
            time.sleep(0.5)
            continue
        if component != [MGAME, NOVEL_LIBRARY]:
            return
        image = phone.frame()
        if vision.match(image, "listen30_library", (82, 124, 351, 216)):
            pause = vision.match(image, "listen30_library_pause", (612, 2083, 709, 2192))
            if pause:
                phone.tap(center(pause))
                time.sleep(0.4)
            phone.back()
            return
        if vision.match(image, "listen30_preview", (300, 1760, 778, 1910)):
            phone.back()
            time.sleep(1.2)
        else:
            time.sleep(0.5)
    raise Stop("未能确认小说章节预览或书库的返回入口。")


def finish_browse10(phone, vision, trace, rule="browse10", *, continue_after_verification=False):
    if rule == "listen30":
        ad_seen = close_novel_ad(phone, vision, trace)
        if ad_seen and phone.foreground_component() == [MGAME, MGAME + ".msv.page.activity.MSVPageActivity"]:
            phone.recover_mgame_main(vision, trace)
    component = phone.foreground_component() if rule in TIMED_PLAYERS else None
    if rule in TIMED_PLAYERS and component in [[MGAME, activity] for activity in TIMED_PLAYERS[rule]]:
        image = timed_media(phone, vision, trace, rule)
    else:
        if rule == "browse10":
            image = phone.frame()
            if (phone.foreground_component() == [MGAME, PROMO_ACTIVITY]
                    and vision.match(image, "browse10_review", (380, 122, 710, 216))):
                trace.snapshot(phone, "resume-review", image=image)
                confirm_browse10_review_exit(phone, vision, trace, resume_image=image)
            elif (not close_share_dialog(phone, vision, trace, image)
                and vision.identify(image)[0] == "unknown" and is_store_detail(image, trace)):
                trace.snapshot(phone, "resume-store", image=image)
                phone.back()
        if rule == "listen30" and component == [MGAME, NOVEL_LIBRARY]:
            leave_novel_audio(phone, vision)
            leave_drama(phone)
        image = game60_panel(phone, vision, trace)
    completed = []
    pending = []
    launch_failures = 0
    moved_attempts = 0
    for index in range(20):
        image, row = scan_game60(phone, vision, image, "either", rule=rule, exclude=completed, exclude_go=tuple(pending))
        if row is None:
            if pending:
                trace.record("timed_verification_pending", rule=rule, confirmed=len(completed), pending=len(pending))
                raise VerificationRequired(f"本次完成 {len(completed)} 项，仍有 {len(pending)} 项需要在手机上验证。")
            trace.record("timed_no_remaining", rule=rule, confirmed=len(completed))
            print(f"已扫到列表底部，本次完成 {len(completed)} 项 {TIMED_SECONDS[rule]} 秒任务。", flush=True)
            return
        y, action, _ = row
        pending_claim = None
        if action == "claim":
            pending_claim = next((i for i, saved in enumerate(pending) if matches_browse10(image, y, saved)), None)
        icon = browse10_key(image, y).copy()
        reward = browse10_reward(image, y, vision, trace, rule)
        try:
            before = tap_browse10_row(phone, vision, trace, icon, action, reward, rule)
            if action == "go":
                if rule == "browse10":
                    image = browse10_landing(phone, vision, trace)
                elif rule == "play20":
                    image = play_game60(phone, vision, trace, seconds=22)
                else:
                    image = timed_media(phone, vision, trace, rule)
                image, row = scan_game60(phone, vision, image, "claim", title=icon, rule=rule)
                if row is None:
                    raise Stop("浏览后原任务未显示可领奖，未确认完成。")
                before = tap_browse10_row(phone, vision, trace, icon, "claim", reward, rule)
        except VerificationRequired:
            if not continue_after_verification or rule != "browse10":
                raise
            pending.append(icon)
            filename = f"browse10-verification-row-{index + 1}.png"
            save_image(trace.directory / filename, icon)
            trace.record("timed_verification_deferred", rule=rule, reward=reward, file=filename)
            print("该浏览项需要人工验证，先核对同阶段其他任务。", flush=True)
            phone.recover_mgame_main(vision, trace)
            image = game60_panel(phone, vision, trace)
            launch_failures = moved_attempts = 0
            continue
        except TaskRowMoved:
            moved_attempts += 1
            if moved_attempts >= 3:
                raise
            trace.snapshot(phone, "row-moved")
            image = game60_panel(phone, vision, trace)
            continue
        except LandingUnconfirmed as exc:
            launch_failures += 1
            if rule not in {"browse10", "listen30"} or launch_failures >= 3:
                raise
            recover_ad_round(phone, vision, trace, exc)
            image = game60_panel(phone, vision, trace)
            continue
        image, after = confirm_reward_count(
            lambda: game60_panel(phone, vision, trace), trace, f"{rule}-{index}-after", before, reward, timeout=10,
        )
        completed.append(icon)
        if pending_claim is not None:
            del pending[pending_claim]
        launch_failures = 0
        moved_attempts = 0
        trace.record("timed_confirmed", rule=rule, before=before, after=after, reward=reward)
        trace.snapshot(phone, rule + "-reward", image=image)
        print(f"计时任务奖励已到账 +{reward}（{before} → {after}）。", flush=True)
    raise Stop("达到浏览任务的单次执行上限。")


def tap_browse10_row(phone, vision, trace, icon, action, reward, rule="browse10"):
    image = phone.frame()
    rows = [row for row in browse10_rows(vision, image, rule)
            if row[1] == action and matches_browse10(image, row[0], icon)]
    if len(rows) != 1:
        raise TaskRowMoved("列表发生移动，未找到原任务行。")
    if browse10_reward(image, rows[0][0], vision, trace, rule) != reward:
        raise Stop("点击前未能核对同一浏览任务、按钮和奖励。")
    before = timed_count(image, trace, f"{rule}-before") if action == "claim" else None
    fresh = phone.frame()
    latest = [row for row in browse10_rows(vision, fresh, rule)
              if row[1] == action and matches_browse10(fresh, row[0], icon)]
    if len(latest) != 1:
        raise TaskRowMoved("列表仍在变化，未能确认任务按钮的位置。")
    y, _, button = latest[0]
    phone.tap(center(button))
    trace.record("timed_tap", rule=rule, action=action, reward=reward, point=center(button))
    save_image(trace.directory / f"{rule}-{action}-row.png", crop(fresh, (35, y - 73, 1045, y + 45)))
    return before


def app_jump_title(image, vision):
    return vision.match(image, "app_jump_title", (175, 700, 620, 2320))


def find_app_jump_list(phone, vision, image):
    for down in (False, True):
        for _ in range(19):
            title = app_jump_title(image, vision)
            if title and title[1] <= 1580:
                return image, title
            if title or down:
                if vision.match(image, "list_end", (410, 1770, 716, 2320)):
                    return None
                phone.scroll(True)
            else:
                if vision.match(image, "list_top", (178, 700, 475, 1000)):
                    break
                phone.scroll(False)
            time.sleep(0.4)
            image = phone.frame()
            if not vision.match(image, "panel", (270, 555, 800, 688)):
                raise LandingUnconfirmed("扫描期间已离开加次数任务列表。")
    raise Stop("未能确认跳转登录 APP 的任务区域。")


def app_jump_progress(image, title, trace, index):
    _, top, right, bottom = title
    text = read_ocr(trace, crop(image, (right, top - 10, right + 160, bottom + 10)),
                    f"app-jump-progress-{index}.png")
    match = re.search(r"(?<!\d)([0-8])[/／]8(?!\d)", re.sub(r"\s+", "", text))
    if match is None:
        raise CountUnreadable("未识别到跳转登录 APP 的 0–8 项进度。")
    return int(match[1])


def app_jump_cards(image, title, vision, trace, index):
    top = title[1] + 108
    area = (175, top, 1045, min(2320, title[1] + 715))
    result = read_ocr(trace, crop(image, area), f"app-jump-cards-{index}.png", layout=True)
    cards = {}
    for line in json.loads(result):
        label = re.sub(r"\s+", "", line["text"])
        for name, package in APP_JUMP_TARGETS.items():
            if name not in label and not (name == "猫耳FM" and "猫FM" in label):
                continue
            x, y = (round(value) for value in line["box"][:2])
            left = 454 if x + area[0] < 620 else 876
            y += top
            region = (left, y - 10, left + 160, min(y + 75, 2330))
            claim = vision.match(image, "app_jump_claim", region, 0.88, competing="app_jump_go")
            go = vision.match(image, "app_jump_go", region, 0.88, competing="app_jump_claim")
            if claim:
                pixels = crop(image, claim)
                orange = (pixels[:, :, 2] > 190) & (pixels[:, :, 1] < 160) & (pixels[:, :, 0] < 100)
                if np.mean(orange) > 0.18:
                    cards[package] = name, "claim", claim
            elif go:
                cards[package] = name, "go", go
    return cards


def visit_app_jump(phone, trace, package):
    deadline = time.monotonic() + 8
    opened = False
    since = None
    elapsed = False
    while time.monotonic() < deadline:
        foreground = phone.foreground()
        if foreground == APP_LOCK:
            trace.record("app_jump_app_lock", package=package)
            break
        now = time.monotonic()
        if foreground == package:
            if since is None:
                since = now
            if not opened:
                opened = True
                deadline = now + 2 * APP_JUMP_WAIT_SECONDS + 5
                trace.record("app_jump_opened", package=package)
            if now - since >= APP_JUMP_WAIT_SECONDS:
                elapsed = True
                trace.record("app_jump_visit_elapsed", package=package, seconds=APP_JUMP_WAIT_SECONDS)
                trace.snapshot(phone, "app-jump-after-wait")
                if phone.foreground() != package:
                    break
                phone.adb_call("shell", "input", "keyevent", "KEYCODE_BACK")
                return_deadline = time.monotonic() + 2
                while time.monotonic() < return_deadline:
                    if phone.foreground() == MGAME:
                        trace.record("app_jump_returned_by_back", package=package)
                        return True
                    time.sleep(0.35)
                break
        elif since is not None:
            since = None
            trace.record("app_jump_visit_interrupted", package=package, foreground=foreground)
        time.sleep(0.35)
    else:
        trace.record("app_jump_visit_unconfirmed", package=package, opened=opened)
    if phone.foreground() != MGAME:
        phone.return_to_mgame()
    return elapsed


def finish_app_jump(phone, vision, trace, attempted):
    installed = set(phone.adb_call("shell", "pm", "list", "packages").decode().splitlines())
    previous = None
    pending = None
    visited = False
    for index in range(17):
        for refresh in range(3):
            image = game60_panel(phone, vision, trace)
            result = find_app_jump_list(phone, vision, image)
            if result is None:
                if previous is None:
                    trace.record("app_jump_not_listed")
                    print("当前列表未显示跳转登录 APP 的任务组。", flush=True)
                    return
                raise Stop("跳转登录 APP 任务已移出列表，未核对最终进度。")
            image, title = result
            sample = f"{index}-{refresh}" if refresh else index
            completed = app_jump_progress(image, title, trace, sample)
            if completed == 8:
                break
            cards = app_jump_cards(image, title, vision, trace, sample)
            candidates = [(package, name, action) for package, (name, action, _) in cards.items()
                          if (package, action) not in attempted]
            if candidates or pending is None or refresh == 2:
                break
            trace.record("app_jump_settlement_wait", package=pending, progress=completed)
            time.sleep(1.5)
        if previous is not None and completed < previous:
            raise Stop(f"跳转登录 APP 进度异常回退：{previous}/8 → {completed}/8。")
        if pending is not None:
            trace.record("app_jump_result", package=pending, before=previous, after=completed)
        trace.record("app_jump_progress", completed=completed, total=8)
        if completed == 8:
            print("跳转登录 APP 任务已核对完成 8/8。", flush=True)
            return
        if not candidates:
            message = f"跳转登录 APP 已核对到 {completed}/8，本轮没有新奖励可领取或未尝试的已识别入口。"
            if visited:
                raise RoundUnsettled(message)
            raise Stop(message)
        package, name, action = min(candidates, key=lambda card: card[2] != "claim")
        previous, pending = completed, package
        if action == "go" and f"package:{package}" not in installed:
            attempted.add((package, action))
            trace.record("app_jump_missing_app", package=package)
            continue
        fresh = phone.frame()
        fresh_title = app_jump_title(fresh, vision)
        if not fresh_title:
            raise LandingUnconfirmed("点击前跳转登录 APP 的任务组已离开当前画面。")
        verified = app_jump_cards(fresh, fresh_title, vision, trace, f"{index}-before-tap").get(package)
        if not verified or verified[1] != action:
            raise LandingUnconfirmed(f"点击前未能重新确认{name}的入口状态。")
        box = verified[2]
        print(f"跳转登录 APP {completed}/8 · {name}", flush=True)
        phone.tap(center(box))
        trace.record("app_jump_tap", package=package, action=action, point=center(box))
        if action == "go" and visit_app_jump(phone, trace, package):
            visited = True
        attempted.add((package, action))
        time.sleep(1)
    raise Stop("跳转登录 APP 达到单次执行上限。")


def run_stages(phone, trace, task_key, visions, settled, *, foreground_games=False):
    stages = [(vision.task.key, vision.task.name, vision) for vision in visions]
    if task_key in {"all", "games"}:
        stages.append(("games", "玩 60 秒得 +3", Vision()))
    for rule, label in TIMED_LABELS.items():
        if task_key in {"all", rule} or (task_key == "timed" and rule != "browse10"):
            stages.append((rule, label, Vision(TASKS[2])))
    if task_key == "appjump":
        stages.append(("appjump", "跳转登录 APP 领奖励", Vision()))

    deferred_promo = False
    deferred_verification = False
    app_jump_attempted = set()
    for number, (key, label, vision) in enumerate(stages, start=1):
        trace.stage = key
        started = time.monotonic()
        print(f"[{number}/{len(stages)}] {label}", flush=True)
        trace.record("stage_started", stage=key, number=number, total=len(stages))
        trace.snapshot(phone, "stage-start")
        recoveries = 0
        deferred = False
        app_jump_restarted = False
        if key not in settled:
            for attempt in range(3):
                try:
                    if key == "games":
                        finish_games(phone, vision, trace, foreground=foreground_games)
                    elif key == "appjump":
                        finish_app_jump(phone, vision, trace, app_jump_attempted)
                    elif key in TIMED_SECONDS:
                        finish_browse10(
                            phone, vision, trace, key,
                            continue_after_verification=key == "browse10" and deferred_verification,
                        )
                    else:
                        finish_task(phone, vision, trace, allow_missing=not (key == "promo" and deferred_promo))
                    break
                except VerificationRequired as exc:
                    if task_key != "all" or key != "browse10" or deferred_verification:
                        raise
                    deferred_verification = deferred = True
                    stages.append((key, label, vision))
                    trace.record("stage_deferred", reason=str(exc))
                    print("浏览任务需要人工验证，先完成后续阶段，最后再打开待处理项。", flush=True)
                    phone.recover_mgame_main(vision, trace)
                    break
                except RoundUnsettled as exc:
                    if key == "appjump" and not app_jump_restarted and attempt < 2:
                        app_jump_restarted = True
                        recoveries += 1
                        trace.record("app_jump_restart_mgame", reason=str(exc))
                        print("跳转奖励暂未刷新，重开美团后重新核对。", flush=True)
                        phone.restart_mgame(vision, trace)
                        continue
                    if task_key != "all" or key != "promo" or deferred_promo:
                        raise
                    deferred_promo = deferred = True
                    stages.append((key, label, vision))
                    trace.record("stage_deferred", reason=str(exc))
                    print("优惠活动暂未结算，先完成后续阶段，最后再补试。", flush=True)
                    break
                except (ForegroundChanged, CaptureUnavailable, LandingUnconfirmed, CountUnreadable, DeviceDisconnected) as exc:
                    if attempt == 2:
                        raise
                    recoveries += 1
                    trace.record("stage_recovery", attempt=attempt + 1, reason=str(exc))
                    if isinstance(exc, DeviceDisconnected):
                        trace.record("usb_reconnect_wait")
                        print("USB 调试连接中断，正在等待原手机重新上线。", flush=True)
                        phone.wait_for_usb()
                        trace.record("usb_reconnected")
                    trace.snapshot(phone, "stage-recovery")
                    phone.recover_mgame_main(vision, trace)
        if deferred:
            continue
        trace.snapshot(phone, "stage-end")
        trace.record("stage_finished", stage=key, recoveries=recoveries,
                     elapsed_seconds=round(time.monotonic() - started, 1))
    trace.stage = None
    if task_key == "all":
        trace.record("all_stages_finished")
        print("自动加次数任务各阶段已处理完毕，请以每项结算和跳过提示为准。", flush=True)


def resume_browsing(phone, trace):
    component = phone.foreground_component()
    package = component[0]
    if package in LANDING_APPS | GOODS_EXTERNAL_APPS | PROMO_EXTERNAL_APPS | {APP_LOCK}:
        trace.snapshot(phone, "resume-external")
        phone.return_to_mgame()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and phone.foreground() != MGAME:
            time.sleep(0.4)
    image = phone.frame()
    vision = Vision()
    if vision.identify(image)[0] == "unknown":
        if close_share_dialog(phone, vision, trace, image):
            pass
        elif is_ranked_promo(image, trace) or is_store_detail(image, trace):
            trace.snapshot(phone, "resume-in-app", image=image)
            phone.back()
        elif (component in GAME_COMPONENTS
              or component == [MGAME, NOVEL_LIBRARY]
              or (package == MGAME and any(component[1] in group for group in TIMED_PLAYERS.values()))):
            trace.snapshot(phone, "resume-player", image=image)
            phone.recover_mgame_main(vision, trace)
        else:
            return image
        time.sleep(0.8)
        image = phone.frame()
    return image


def open_game_center(phone, trace):
    link_tapped = False
    prompt_tapped = False
    for step in range(8):
        image = phone.frame()
        trace.snapshot(phone, "game-center-navigation", image=image)
        result = read_ocr(trace, image, f"navigation-ocr-{step + 1}.png", layout=True)
        lines = [(re.sub(r"\s+", "", line["text"]), line["box"]) for line in json.loads(result)]
        text = "".join(label for label, _ in lines)
        if re.search(r"桌面入[口囗]提示", text):
            if prompt_tapped:
                time.sleep(0.8)
                continue
            dismiss = [box for label, box in lines if label == "不需要"]
            if len(dismiss) != 1:
                raise Stop("未确认桌面入口提示的关闭按钮。")
            point = center(tuple(round(value) for value in dismiss[0]))
            phone.tap(point)
            trace.record("game_center_desktop_prompt_tapped", point=point)
            prompt_tapped = True
            time.sleep(0.8)
            continue
        if "玩游戏" in text and "好游推荐" in text and "最近在玩" in text:
            trace.record("game_center_ready")
            print("已确认打开游戏中心「玩游戏」首页。", flush=True)
            return
        if not text or link_tapped or prompt_tapped:
            time.sleep(1)
            continue
        links = [box for label, box in lines if label == "游戏中心"]
        if len(links) == 1:
            point = center(tuple(round(value) for value in links[0]))
            phone.tap(point)
            trace.record("game_center_link_tapped", point=point)
            link_tapped = True
        else:
            phone.back()
            trace.record("game_center_navigation_back", step=step)
        time.sleep(0.8)
    raise Stop("未确认游戏中心首页，请查看程序导航截图。")


def main():
    parser = argparse.ArgumentParser(description="自动加次数任务 · Mgame 本地助手")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="按页面进度完成剩余轮次")
    mode.add_argument("--image", type=Path, help="离线识别指定截图，不连接手机")
    mode.add_argument("--return-mgame", action="store_true", help="从手机桌面和最近任务返回Mgame主界面")
    mode.add_argument("--open-game-center", action="store_true", help="返回并通过游戏中心链接进入首页")
    parser.add_argument("--serial", help="ADB USB 真机序列号，省略时选择唯一已授权的真机")
    parser.add_argument("--task", choices=("all", *(task.key for task in TASKS), "games", *TIMED_SECONDS, "timed", "appjump"), default="all")
    args = parser.parse_args()

    trace = None
    phone = None
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        with (ROOT / "session.lock").open("a+b") as lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise Stop("已有一个助手实例正在运行。") from exc
            if args.return_mgame:
                trace = Trace("return_mgame")
                Phone(args.serial).recover_mgame_main(Vision(), trace)
                print("已确认返回Mgame主界面。", flush=True)
                return 0
            if args.open_game_center:
                trace = Trace("open_game_center")
                phone = Phone(args.serial)
                open_game_center(phone, trace)
                return 0
            trace = Trace("execute" if args.execute else "offline" if args.image else "preview")
            visions = [Vision(task) for task in TASKS if args.task in {"all", task.key}]
            phone = None if args.image else Phone(args.serial)
            if args.execute:
                settled = set()
                current = resume_browsing(phone, trace) if visions else None
                for vision in visions:
                    state, _ = observe(current, vision, trace)
                    if state == "promo_landing":
                        browse_promo_page(phone, vision, trace)
                        wait_for_page(phone, vision, {"reward", "reward_final"}, timeout=12)
                        dismiss_reward(phone, vision, trace)
                        break
                    if state == "ad":
                        dismiss_ad(phone, vision, trace)
                        wait_for_page(phone, vision, {"coins", "list", "task", "task_unavailable"})
                        break
                    if state in {"reward", "reward_final"}:
                        dismissed, _ = dismiss_reward(phone, vision, trace)
                        if dismissed == "reward_final" and sum(task.badge == vision.task.badge for task in TASKS) == 1:
                            trace.record("task_complete", task=vision.task.key, evidence="final_reward_dialog")
                            settled.add(vision.task.key)
                        break
                run_stages(phone, trace, args.task, visions, settled)
            else:
                image = load_image(args.image) if args.image else phone.frame()
                check_size(image)
                if args.task == "appjump":
                    vision = Vision(TASKS[2])
                    title = app_jump_title(image, vision)
                    if not title:
                        raise Stop("当前画面未显示跳转登录 APP 的任务组。")
                    completed = app_jump_progress(image, title, trace, 0)
                    print(f"当前跳转登录 APP 进度 {completed}/8。")
                    return 0
                if args.task in {"games", *TIMED_SECONDS, "timed"}:
                    vision = Vision()
                    rules = ("play20", "listen30", "watch60") if args.task == "timed" else (args.task,)
                    matches = (list(game60_rows(vision, image)) if args.task == "games" else
                               [row for rule in rules for row in browse10_rows(vision, image, rule)])
                    for y, action, box in matches:
                        save_image(trace.directory / f"{args.task}-preview-{y}-{action}.png",
                                   crop(image, (26, y - 81, 1045, y + 62)))
                    print(f"当前画面识别到 {len(matches)} 项可操作的计时任务。")
                    return 0
                vision = next(
                    (item for item in visions if item.identify(image)[0] in {"task", "ad", "reward", "reward_final"}),
                    visions[0],
                )
                state, box = vision.identify(image)
                trace.evidence(image, state, box, "preview.png")
                trace.record("preview", state=state, point=center(box) if box else None)
                if state in {"task", "task_unavailable"}:
                    done, total = read_progress(image, vision, trace, "preview")
                    print(f"任务进度 {done}/{total}，剩余 {total - done} 轮。")
                if box:
                    print(f"试运行：{state}，建议点击 {center(box)}。未发送输入。")
                elif state == "list":
                    print("已识别任务列表。当前画面没有完整目标行，试运行不滚动。")
                elif state in {"reward", "reward_final"}:
                    print(f"当前是 +{vision.task.reward} 结算弹窗。")
                else:
                    raise Stop("当前页面未发现可执行的目标。")
        return 0
    except (Stop, OSError, ValueError, KeyError, cv2.error) as exc:
        if trace:
            trace.record("stopped", reason=str(exc))
            if phone is not None and (args.execute or args.open_game_center):
                trace.snapshot(phone, "stopped")
        print(f"已停止：{exc}", file=sys.stderr)
        return 2
    finally:
        if trace:
            print(f"运行记录：{trace.directory}")


if __name__ == "__main__":
    raise SystemExit(main())
