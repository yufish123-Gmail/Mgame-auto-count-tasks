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
PROMO_LIVE = "com.dianping.live.live.mrn.MLiveMRNActivity"
PROMO_GROUP = "com.meituan.android.mtgb.business.main.MTGMainActivity"
PROMO_ATTRACTION = "com.meituan.traveltools.mrncontainer.HTMRNBaseActivity"
AD_WEBVIEW = "com.ubix.ssp.open.comm.UBiXWebViewActivity"
PROMO_EXTERNAL_APPS = {"com.dianping.v1"}
GOODS_EXTERNAL_APPS = {"com.qiyi.video", "com.UCMobile", "com.tencent.qqlive"}
GAME_ACTIVITY = "com.meituan.android.mgc.container.MGCGameActivity"
GAME_AD = "com.windmill.sdk.widget.InterstitialView_4012001"
GAME_AD_COMPACT = "com.windmill.sdk.widget.InterstitialView_4012003"
GAME60_AD_COMPONENTS = ([MGAME, GAME_AD], [MGAME, GAME_AD_COMPACT])
GAME_COMPONENTS = ([MGAME, GAME_ACTIVITY], *GAME60_AD_COMPONENTS)
GAME_WAIT_SECONDS = 62
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


class ForegroundChanged(Stop):
    pass


class CaptureUnavailable(Stop):
    pass


class LandingUnconfirmed(Stop):
    pass


class TaskRowMoved(Stop):
    pass


def run_command(*arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Stop(f"ADB 执行失败：{exc}") from exc
    error = result.stderr.decode("utf-8", errors="replace").strip()
    output = result.stdout.decode("utf-8", errors="replace") if arguments[-2:] != ("screencap", "-p") else ""
    if result.returncode or "SecurityException" in error + output or "Error:" in output:
        raise Stop(f"ADB 执行失败：{error or output.strip() or result.returncode}")
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
        raise Stop("无法读取截图。")
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

    def capture(self):
        image = decode(self.adb_call("exec-out", "screencap", "-p"))
        check_size(image)
        return image

    def frame(self):
        self.require_mgame()
        image = self.capture()
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
            image = self.capture()
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
                raise Stop("切换后未确认Mgame主界面已打开。")
            time.sleep(0.4)
        trace.record("recovery_main_confirmed")


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

    def match(self, image, name, region, minimum=0.90, competing=None):
        template = self.templates[name]
        view = cv2.cvtColor(crop(image, region), cv2.COLOR_BGR2GRAY)
        height, width = template.shape
        if height > view.shape[0] or width > view.shape[1]:
            return None
        scores = cv2.matchTemplate(view, template, cv2.TM_CCOEFF_NORMED)
        _, score, _, (x, y) = cv2.minMaxLoc(scores)
        if score < minimum:
            return None
        if competing:
            other = self.templates[competing]
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
        if re.sub(r"\s+", "", text) != "点击并浏览10秒领奖励":
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


def read_ocr(trace, image, filename):
    path = trace.directory / filename
    save_image(path, image)
    reader = Path(__file__).resolve().parent / "read-progress.ps1"
    return run_command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                       "-File", str(reader), "-Image", str(path)).decode("utf-8", errors="replace").strip()


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
                output = read_ocr(trace, retry, f"progress-{label}-retry-{scale}.png")
                values = re.findall(r"(?<!\d)(\d{1,2})\s*[/／]\s*(\d{1,2})(?!\d)", output)
                values = [value for value in values if 0 <= int(value[0]) <= int(value[1]) <= 30 and int(value[1]) > 0]
                if len(values) == 1:
                    break
    if len(values) != 1:
        raise Stop(f"未能读取任务进度，请检查 {path.name}。")
    done, total = map(int, values[0])
    if not 0 <= done <= total <= 30 or total == 0:
        raise Stop(f"无法确认任务进度 {done}/{total}。")
    trace.record("progress", completed=done, total=total)
    return done, total


class Trace:
    def __init__(self, mode):
        self.directory = ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.directory.mkdir(parents=True)
        self.stage = None
        self.screenshot_index = 0
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
            image = phone.capture() if image is None else image
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


def close_ad_error(phone, vision, image, trace):
    if phone.foreground_component() != [MGAME, AD_WEBVIEW]:
        return False
    if not vision.match(image, "ad_web_error", (340, 112, 744, 233)):
        return False
    close = vision.match(image, "ad_web_close", (150, 112, 263, 228))
    if close is None:
        return False
    phone.tap(center(close))
    trace.record("ad_error_closed", point=center(close))
    return True


def wait_for_page(phone, vision, wanted, timeout=10, trace=None, *, settling=False):
    deadline = time.monotonic() + timeout
    state = "unknown"
    entry_failed = False
    closed_error = False
    while time.monotonic() < deadline:
        try:
            image = phone.frame()
        except ForegroundChanged:
            if "promo_landing" in wanted and phone.foreground() in PROMO_EXTERNAL_APPS:
                return None, "promo_external", None
            time.sleep(0.45)
            continue
        except CaptureUnavailable:
            time.sleep(0.45)
            continue
        if "ad" in wanted:
            failed = vision.match(image, "entry_failed", (290, 1090, 795, 1275), 0.87)
            if failed:
                if trace:
                    save_image(trace.directory / "entry-failed.png", crop(image, failed))
                entry_failed = True
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
            elif component == [MGAME, PROMO_LIVE]:
                return image, "promo_live", None
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
        if state == "unknown" and settling and not closed_error:
            closed_error = close_ad_error(phone, vision, image, trace)
            if closed_error:
                deadline = time.monotonic() + timeout
        time.sleep(0.45)
    if entry_failed:
        raise LandingUnconfirmed("Mgame提示「操作失败，请稍后重试」，未确认本轮开始。")
    raise LandingUnconfirmed(f"等待页面超时，预期 {', '.join(sorted(wanted))}，最后识别为 {state}。")


def tap_verified(phone, vision, trace, expected, label="", progress=None):
    image = phone.frame()
    state, box = observe(image, vision, trace)
    if state != expected or box is None:
        raise Stop(f"点击前页面已变化：{state}。")
    if progress is not None and read_progress(image, vision, trace, f"{label}-before-tap") != progress:
        raise Stop("点击前任务进度已变化。")
    trace.evidence(image, state, box, f"click-{state}{label}.png")
    trace.record("tap", state=state, point=center(box))
    phone.tap(center(box))


def dismiss_ad(phone, vision, trace):
    image = phone.frame()
    if observe(image, vision, trace)[0] != "ad":
        raise Stop("关闭前广告页面已变化。")
    close = vision.match(image, "ad_close", (479, 1675, 603, 1890))
    if close is None:
        raise Stop("无法确认已有广告的关闭按钮。")
    phone.tap(center(close))
    trace.record("ad_dismissed")


def find_task(phone, vision, trace, *, allow_missing=False):
    image = phone.frame()
    state, _ = vision.identify(image)
    if state == "profile":
        tap_verified(phone, vision, trace, "profile")
        image, state, _ = wait_for_page(phone, vision, {"coins"})
    if state == "coins":
        tap_verified(phone, vision, trace, "coins")
        listing = {"list", "task", "task_unavailable"}
        image, state, _ = wait_for_page(phone, vision, listing | {"reward", "reward_final"})
        if state not in listing:
            dismiss_reward(phone, vision, trace)
            image, state, _ = wait_for_page(phone, vision, listing)
    saw_top = False
    for down in (False, True):
        still = 0
        for step in range(17):
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


def browse_external(phone, vision, trace):
    landing_apps = LANDING_APPS | PROMO_EXTERNAL_APPS | GOODS_EXTERNAL_APPS
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
    trace.record("landing", package=package)
    trace.snapshot(phone, "app-lock" if package == APP_LOCK else "external-landing")
    if package != APP_LOCK:
        until = time.monotonic() + vision.task.seconds + 2
        last_allowed = time.monotonic()
        while time.monotonic() < until:
            foreground = phone.foreground()
            if foreground == APP_LOCK:
                package = APP_LOCK
                break
            if foreground == package:
                last_allowed = time.monotonic()
            elif foreground in landing_apps:
                trace.record("landing_changed", previous=package, package=foreground)
                package = foreground
                last_allowed = time.monotonic()
                until = last_allowed + vision.task.seconds + 2
            elif foreground is not None or time.monotonic() - last_allowed > 1.5:
                raise LandingUnconfirmed(f"浏览期间前台应用变化：{foreground}。")
            time.sleep(0.35)
        if package != APP_LOCK and phone.foreground() != package:
            raise LandingUnconfirmed("计时结束时广告落地页不在前台。")
    if package == APP_LOCK:
        trace.record("app_lock_return")
    phone.return_to_mgame()
    return package == APP_LOCK


def browse_one(phone, vision, trace, before, progress):
    label = f"-{vision.task.key}-{progress[0] + 1}"
    tap_verified(phone, vision, trace, "task", label, progress)
    wanted = {"ad", "promo_landing"} if vision.task.key == "promo" else {"ad"}
    landing, state, _ = wait_for_page(phone, vision, wanted, timeout=18, trace=trace)
    if landing is not None:
        trace.snapshot(phone, state, image=landing)
    locked = False
    if state in {"promo_landing", "promo_travel", "promo_live", "promo_farm", "promo_attraction", "promo_rank"}:
        browse_promo_page(phone, vision, trace, page=state)
    elif state == "promo_external":
        locked = browse_external(phone, vision, trace)
    else:
        tap_verified(phone, vision, trace, "ad", label)
        locked = browse_external(phone, vision, trace)
    wanted = {"reward", "reward_final"}
    if locked:
        wanted.add("ad")
    after, state, _ = wait_for_page(
        phone, vision, wanted, timeout=12, trace=trace,
        settling=True,
    )
    if state == "ad":
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
            break
        phone.tap(center(close))
        trace.record("live_closed", target="follow_popup" if follow else "room", point=center(close))
        time.sleep(0.75)
    if phone.foreground_component() != [MGAME, PROMO_LIVE]:
        return
    raise Stop("无法确认直播页关闭按钮。")


def browse_promo_page(phone, vision, trace, *, page="promo_landing"):
    trace.record("landing", page=page, duration=vision.task.seconds + 2)
    activity = {
        "promo_travel": PROMO_WEBVIEW, "promo_live": PROMO_LIVE, "promo_farm": GAME_ACTIVITY,
        "promo_attraction": PROMO_ATTRACTION,
    }.get(page)
    if page == "promo_rank":
        activity = phone.foreground_component()[1]
    deadline = time.monotonic() + vision.task.seconds + 2
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
    state, _ = vision.identify(image)
    if state == "reward":
        close = vision.match(image, "reward_close", (110, 1490, 512, 1640), 0.87)
    elif state == "reward_final":
        close = vision.match(image, "reward_final", (390, 1525, 690, 1670))
    else:
        return
    if close is None:
        raise Stop("无法确认奖励弹窗的关闭按钮。")
    phone.tap(center(close))
    trace.record("reward_dismissed")
    wait_for_page(phone, vision, {"coins", "list", "task", "task_unavailable"})
    return state


def recover_ad_round(phone, vision, trace, failure):
    trace.record("ad_recovery", reason=str(failure))
    trace.snapshot(phone, "ad-recovery")
    if phone.foreground() != MGAME:
        phone.return_to_mgame()
    else:
        image = phone.frame()
        state, _ = observe(image, vision, trace)
        if state == "unknown" and not close_ad_error(phone, vision, image, trace):
            phone.back()
    targets = {"coins", "profile", "list", "task", "task_unavailable", "ad", "reward", "reward_final"}
    try:
        _, state, _ = wait_for_page(phone, vision, targets, timeout=6, trace=trace, settling=True)
    except LandingUnconfirmed:
        phone.recover_mgame_main(vision, trace)
        return None
    if state == "ad":
        dismiss_ad(phone, vision, trace)
        image, state, _ = wait_for_page(phone, vision, targets - {"ad"}, timeout=6)
        if state in {"reward", "reward_final"}:
            trace.snapshot(phone, state, image=image)
    return state if state in {"reward", "reward_final"} else None


def finish_task(phone, vision, trace, *, allow_missing=False):
    if dismiss_reward(phone, vision, trace) == "reward_final":
        trace.record("task_complete", task=vision.task.key, evidence="final_reward_dialog")
        print("Mgame已显示最后一轮结算，剩余 0 轮。")
        return
    frame = find_task(phone, vision, trace, allow_missing=allow_missing)
    if frame is None:
        trace.record("task_not_listed", task=vision.task.key)
        print(f"全列表未显示“{vision.task.name}”，跳过。", flush=True)
        return
    progress = read_progress(frame, vision, trace, f"{vision.task.key}-start")
    done, total = progress
    print(f"{vision.task.name} 当前进度 {done}/{total}，剩余 {total - done} 轮。", flush=True)
    for expected in range(done + 1, total + 1):
        for _ in range(4):
            try:
                reward = browse_one(phone, vision, trace, frame, progress)
            except LandingUnconfirmed as exc:
                reward = recover_ad_round(phone, vision, trace, exc)
            if reward is not None:
                break
            frame = find_task(phone, vision, trace)
            refreshed = read_progress(frame, vision, trace, f"{vision.task.key}-retry-{expected}")
            if refreshed == (expected, total):
                progress = refreshed
                reward = "progress_confirmed"
                break
            if refreshed != progress:
                raise Stop(f"广告关闭后进度异常变化：{refreshed[0]}/{refreshed[1]}。")
        else:
            raise Stop("本轮尝试四次后仍未核对到结算。")
        if reward == "progress_confirmed":
            print(f"恢复后已核对进度 {expected}/{total}。", flush=True)
            continue
        dismissed = dismiss_reward(phone, vision, trace)
        if reward == "reward_final" or dismissed == "reward_final":
            if expected != total:
                raise Stop(f"Mgame已结束本组任务，上一轮进度为 {progress[0]}/{total}。")
            print(f"最后一轮已结算，任务达到 {total}/{total}。", flush=True)
            break
        frame = find_task(phone, vision, trace)
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
    raise Stop("无法读取游戏任务的领币次数。")


def timed_count(image, trace, tag):
    gray = cv2.cvtColor(crop(image, (87, 402, 160, 451)), cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY_INV)
    scaled = cv2.resize(binary, None, fx=3, fy=3, interpolation=cv2.INTER_LINEAR)
    padded = cv2.copyMakeBorder(scaled, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    value = read_ocr(trace, padded, f"timed-count-{tag}.png")
    if re.fullmatch(r"\d{1,4}", value):
        return int(value)
    return game_count(image, trace, tag)


def game60_panel(phone, vision, trace):
    listing = {"list", "task", "task_unavailable"}
    entrance = {"profile", "coins"} | listing
    image, state, _ = wait_for_page(phone, vision, entrance | {"reward", "reward_final"}, timeout=15)
    if state in {"reward", "reward_final"}:
        dismiss_reward(phone, vision, trace)
        image, state, _ = wait_for_page(phone, vision, entrance)
    if state == "profile":
        tap_verified(phone, vision, trace, "profile")
        _, state, _ = wait_for_page(phone, vision, {"coins"})
    if state == "coins":
        tap_verified(phone, vision, trace, "coins")
        image, _, _ = wait_for_page(phone, vision, listing)
    trace.record("game60_panel_open")
    return image


def scan_game60(phone, vision, image, state, title=None, *, rule=None, exclude=()):
    for down in (False, True):
        still = 0
        for _ in range(20):
            if state in {"claim", "either"} or down:
                rows = browse10_rows(vision, image, rule) if rule else game60_rows(vision, image)
                if rule:
                    rows = (row for row in rows if (title is None or matches_browse10(image, row[0], title))
                            and not any(matches_browse10(image, row[0], icon) for icon in exclude))
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


def play_game60(phone, vision, trace, seconds=GAME_WAIT_SECONDS):
    deadline = time.monotonic() + 45
    while phone.foreground_component() not in GAME_COMPONENTS:
        if time.monotonic() >= deadline:
            raise Stop("未能确认游戏打开。")
        time.sleep(0.5)

    trace.snapshot(phone, "game-open")
    launcher = phone.home()
    deadline = time.monotonic() + 5
    while phone.foreground() != launcher:
        if time.monotonic() >= deadline:
            raise Stop("按 Home 后未确认回到手机桌面。")
        time.sleep(0.4)
    trace.record("game60_background_started", seconds=seconds)
    trace.snapshot(phone, "game-desktop")
    print(f"已回到手机桌面，等待 {seconds} 秒后切回Mgame。", flush=True)
    time.sleep(seconds)
    trace.record("game60_timer_elapsed", seconds=seconds, mode="background")
    phone.return_to_mgame()
    try:
        image = return_game60_panel(phone, vision, trace)
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
    button = vision.match(image, "game60_exit", (925, 25, 1079, 210), 0.88)
    if not button:
        raise Stop("未识别到小游戏的退出按钮。")
    phone.tap(center(button))
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


def finish_games(phone, vision, trace):
    component = phone.foreground_component()
    if component in GAME_COMPONENTS:
        image = play_game60(phone, vision, trace)
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
            image = play_game60(phone, vision, trace)
            image, row = scan_game60(phone, vision, image, "claim", title=selected)
            if row is None:
                raise Stop("停留后没有看到“可领奖”，未确认游戏任务完成。")
        else:
            selected = game60_title(image, row[0]).copy()
        fresh = phone.frame()
        _, _, box = same_game60(fresh, game60_rows(vision, fresh), selected, "claim")
        before = game_count(fresh, trace, f"{index}-before")
        phone.tap(center(box))
        time.sleep(2)
        image = phone.frame()
        after = game_count(image, trace, f"{index}-after")
        if after != before + 3:
            raise Stop(f"游戏领奖次数变化异常：{before} → {after}，未确认 +3。")
        trace.record("game60_confirmed", before=before, after=after)
        trace.snapshot(phone, "game-reward", image=image)
        print(f"游戏奖励已到账 +3（{before} → {after}）。", flush=True)
    raise Stop("达到单次执行上限。")


def is_store_detail(image, trace):
    tabs = crop(image, (24, 1354, 710, 1450))
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
                trace.snapshot(phone, "browse-landing", image=image, component=component)
                time.sleep(12)
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


def confirm_browse10_review_exit(phone, vision, trace):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if phone.foreground_component() != [MGAME, PROMO_ACTIVITY]:
            return
        image = phone.frame()
        dialog = vision.match(image, "browse10_review_prompt", (330, 1092, 734, 1215))
        button = vision.match(image, "browse10_review_leave", (190, 1380, 495, 1480))
        if dialog and button:
            phone.tap(center(button))
            trace.record("browse10_review_exit_confirmed")
            break
        time.sleep(0.5)
    else:
        raise Stop("未确认评审团的离开按钮。")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if phone.foreground_component() != [MGAME, PROMO_ACTIVITY]:
            return
        if vision.match(phone.frame(), "browse10_review_home", (370, 1564, 708, 1695)):
            phone.back()
            return
        time.sleep(0.5)
    raise Stop("未确认评审团首页已加载。")


def timed_media(phone, vision, trace, rule):
    listening = rule == "listen30"
    players = [[MGAME, activity] for activity in TIMED_PLAYERS[rule]]
    deadline = time.monotonic() + 18
    while phone.foreground_component() not in players:
        if time.monotonic() >= deadline:
            raise Stop("未识别计时任务的播放页面。")
        time.sleep(0.5)
    if listening:
        start_novel_audio(phone, vision)
    seconds = TIMED_SECONDS[rule] + 2
    trace.record("timed_media_started", rule=rule, seconds=seconds)
    trace.snapshot(phone, rule + "-playing")
    print(f"已打开播放页，前台停留 {seconds} 秒。", flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if phone.foreground_component() not in players:
            raise Stop("计时期间播放页离开前台。")
        time.sleep(0.5)
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


def start_novel_audio(phone, vision):
    pause_region = (156, 1905, 243, 2027)
    audio_region = (798, 1880, 1045, 1982)
    ui_deadline = time.monotonic() + 8
    while time.monotonic() < ui_deadline:
        image = phone.frame()
        if vision.match(image, "listen30_popup", (133, 475, 845, 566)):
            close = vision.match(image, "listen30_popup_close", (874, 480, 942, 558))
            if close:
                phone.tap(center(close))
        elif vision.match(image, "listen30_pause", pause_region):
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
        raise Stop("未确认听书控件，无法开始播放。")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if vision.match(phone.frame(), "listen30_pause", pause_region):
            return
        time.sleep(0.5)
    raise Stop("未确认小说开始播放。")


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


def finish_browse10(phone, vision, trace, rule="browse10"):
    component = phone.foreground_component() if rule in TIMED_PLAYERS else None
    if rule in TIMED_PLAYERS and component in [[MGAME, activity] for activity in TIMED_PLAYERS[rule]]:
        image = timed_media(phone, vision, trace, rule)
    else:
        if rule == "browse10":
            image = phone.frame()
            if (not close_share_dialog(phone, vision, trace, image)
                and vision.identify(image)[0] == "unknown" and is_store_detail(image, trace)):
                trace.snapshot(phone, "resume-store", image=image)
                phone.back()
        if rule == "listen30" and component == [MGAME, NOVEL_LIBRARY]:
            leave_novel_audio(phone, vision)
            leave_drama(phone)
        image = game60_panel(phone, vision, trace)
    completed = []
    launch_failures = 0
    moved_attempts = 0
    for index in range(20):
        image, row = scan_game60(phone, vision, image, "either", rule=rule, exclude=completed)
        if row is None:
            trace.record("timed_no_remaining", rule=rule, confirmed=len(completed))
            print(f"已扫到列表底部，本次完成 {len(completed)} 项 {TIMED_SECONDS[rule]} 秒任务。", flush=True)
            return
        y, action, _ = row
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
        except TaskRowMoved:
            moved_attempts += 1
            if moved_attempts >= 3:
                raise
            trace.snapshot(phone, "row-moved")
            image = game60_panel(phone, vision, trace)
            continue
        except LandingUnconfirmed as exc:
            launch_failures += 1
            if rule != "browse10" or launch_failures >= 3:
                raise
            recover_ad_round(phone, vision, trace, exc)
            image = game60_panel(phone, vision, trace)
            continue
        deadline = time.monotonic() + 10
        while True:
            image = game60_panel(phone, vision, trace)
            after = timed_count(image, trace, f"{rule}-{index}-after")
            if after == before + reward or time.monotonic() >= deadline:
                break
            time.sleep(0.6)
        if after != before + reward:
            raise Stop(f"浏览任务领币次数变化异常：{before} → {after}，预期 +{reward}。")
        completed.append(icon)
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


def run_stages(phone, trace, task_key, visions, settled):
    stages = [(vision.task.key, vision.task.name, vision) for vision in visions]
    if task_key in {"all", "games"}:
        stages.append(("games", "玩 60 秒得 +3", Vision()))
    for rule, label in TIMED_LABELS.items():
        if task_key in {"all", rule} or (task_key == "timed" and rule != "browse10"):
            stages.append((rule, label, Vision(TASKS[2])))

    for number, (key, label, vision) in enumerate(stages, start=1):
        trace.stage = key
        started = time.monotonic()
        print(f"[{number}/{len(stages)}] {label}", flush=True)
        trace.record("stage_started", stage=key, number=number, total=len(stages))
        trace.snapshot(phone, "stage-start")
        if key not in settled:
            if key == "games":
                finish_games(phone, vision, trace)
            elif key in TIMED_SECONDS:
                finish_browse10(phone, vision, trace, key)
            else:
                finish_task(phone, vision, trace, allow_missing=True)
        trace.snapshot(phone, "stage-end")
        trace.record("stage_finished", stage=key, elapsed_seconds=round(time.monotonic() - started, 1))
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


def main():
    parser = argparse.ArgumentParser(description="自动加次数任务 · Mgame 本地助手")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="按页面进度完成剩余轮次")
    mode.add_argument("--image", type=Path, help="离线识别指定截图，不连接手机")
    mode.add_argument("--return-mgame", action="store_true", help="从手机桌面和最近任务返回Mgame主界面")
    parser.add_argument("--serial", help="ADB USB 真机序列号，省略时选择唯一已授权的真机")
    parser.add_argument("--task", choices=("all", *(task.key for task in TASKS), "games", *TIMED_SECONDS, "timed"), default="all")
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
                        same_reward = [item for item in visions if item.task.badge == vision.task.badge]
                        if dismiss_reward(phone, vision, trace) == "reward_final" and len(same_reward) == 1:
                            trace.record("task_complete", task=vision.task.key, evidence="final_reward_dialog")
                            settled.add(vision.task.key)
                        break
                run_stages(phone, trace, args.task, visions, settled)
            else:
                image = load_image(args.image) if args.image else phone.frame()
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
            if phone is not None and args.execute:
                trace.snapshot(phone, "stopped")
        print(f"已停止：{exc}", file=sys.stderr)
        return 2
    finally:
        if trace:
            print(f"运行记录：{trace.directory}")


if __name__ == "__main__":
    raise SystemExit(main())
