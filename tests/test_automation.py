import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import coin_helper as helper


class Clock:
    def __init__(self):
        self.now = 0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.phone = MagicMock()
        self.trace = MagicMock()
        self.vision = MagicMock(task=helper.TASKS[0])

    def test_recovery_retries_only_the_current_stage(self):
        second = MagicMock(task=helper.TASKS[1])
        with patch.object(helper, "finish_task", side_effect=[None, helper.CaptureUnavailable("busy"), None]) as finish:
            helper.run_stages(self.phone, self.trace, "page", [self.vision, second], set())
        self.assertEqual([call.args[1] for call in finish.call_args_list], [self.vision, second, second])
        self.phone.recover_mgame_main.assert_called_once_with(second, self.trace)
        finished = [call.kwargs for call in self.trace.record.call_args_list if call.args[0] == "stage_finished"]
        self.assertEqual([entry["recoveries"] for entry in finished], [0, 1])

    def test_progress_skips_ocr_only_when_fresh_pixels_and_task_match(self):
        self.trace.directory = Path("runs")
        self.trace.last_progress = None
        self.vision.task_title.return_value = (35, 20, 100, 40)
        image = helper.np.full((80, 300, 3), 255, dtype=helper.np.uint8)
        image[24:34, 117:123] = 0
        changed = image.copy()
        changed[30:39, 125:131] = 0
        with patch.object(helper, "read_ocr", side_effect=["1/5", "2/5", "2/10"]) as ocr:
            self.assertEqual(helper.read_progress(image, self.vision, self.trace, "settled"), (1, 5))
            self.assertEqual(helper.read_progress(image.copy(), self.vision, self.trace, "before-tap"), (1, 5))
            self.assertEqual(helper.read_progress(changed, self.vision, self.trace, "changed"), (2, 5))
            self.vision.task = helper.TASKS[1]
            self.assertEqual(helper.read_progress(changed, self.vision, self.trace, "other-task"), (2, 10))
        self.assertEqual(ocr.call_count, 3)
        self.trace.record.assert_any_call("progress", completed=1, total=5, ocr_reused_from="progress-settled.png")

    def test_find_task_handles_reward_that_appeared_after_recovery(self):
        popup, task_page = object(), object()
        self.phone.frame.return_value = popup
        self.vision.identify.return_value = ("reward", None)
        with patch.object(helper, "dismiss_reward", return_value=("reward", (task_page, "task", None))):
            self.assertIs(helper.find_task(self.phone, self.vision, self.trace), task_page)
        self.trace.snapshot.assert_called_once_with(self.phone, "list-late-reward", image=popup)
        self.phone.scroll.assert_not_called()

    def test_browse_verification_stops_without_input_or_retry(self):
        component = [helper.MGAME, helper.VERIFICATION_ACTIVITY]
        self.phone.foreground_component.return_value = component
        with self.assertRaises(helper.Stop) as failure:
            helper.browse10_landing(self.phone, self.vision, self.trace)
        self.assertNotIsInstance(failure.exception, helper.LandingUnconfirmed)
        self.trace.snapshot.assert_called_once_with(self.phone, "verification-required", component=component)
        self.phone.tap.assert_not_called()
        self.phone.back.assert_not_called()

    def test_game_still_foreground_after_main_recovery_gets_one_restart(self):
        clock = Clock()
        self.phone.home.return_value = "launcher"
        self.phone.foreground.side_effect = ["launcher", helper.MGAME]
        self.phone.foreground_component.return_value = [helper.MGAME, helper.GAME_ACTIVITY]
        with patch.object(helper, "time", clock):
            helper.Phone.recover_mgame_main(self.phone, self.vision, self.trace)
        self.phone.restart_mgame.assert_called_once_with(self.vision, self.trace)
        self.trace.record.assert_any_call("recovery_stuck_game_restart")

    def test_recovery_stops_after_three_stage_attempts(self):
        with patch.object(helper, "finish_task", side_effect=helper.LandingUnconfirmed("unknown")) as finish:
            with self.assertRaises(helper.LandingUnconfirmed):
                helper.run_stages(self.phone, self.trace, "page", [self.vision], set())
        self.assertEqual(finish.call_count, 3)
        self.assertEqual(self.phone.recover_mgame_main.call_count, 2)
        self.assertFalse(any(call.args[0] == "stage_finished" for call in self.trace.record.call_args_list))

    def test_reward_mismatch_is_not_retried(self):
        with patch.object(helper, "finish_task", side_effect=helper.Stop("reward mismatch")) as finish:
            with self.assertRaises(helper.Stop):
                helper.run_stages(self.phone, self.trace, "page", [self.vision], set())
        finish.assert_called_once()
        self.phone.recover_mgame_main.assert_not_called()

    def test_usb_loss_waits_before_resuming_the_current_stage(self):
        disconnected = helper.DeviceDisconnected("device not found")
        with patch.object(helper, "finish_task", side_effect=[disconnected, None]) as finish:
            helper.run_stages(self.phone, self.trace, "page", [self.vision], set())
        self.assertEqual(finish.call_count, 2)
        self.phone.wait_for_usb.assert_called_once()
        self.phone.recover_mgame_main.assert_called_once_with(self.vision, self.trace)
        events = [call.args[0] for call in self.trace.record.call_args_list]
        self.assertLess(events.index("usb_reconnect_wait"), events.index("usb_reconnected"))
        self.assertLess(events.index("usb_reconnected"), events.index("stage_finished"))

    def test_usb_wait_checks_the_original_phone_and_times_out(self):
        phone = helper.Phone.__new__(helper.Phone)
        phone.adb, phone.serial = "adb", "original"
        with (
            patch.object(helper, "time", Clock()),
            patch.object(helper, "run_command", side_effect=helper.DeviceDisconnected("offline")) as run,
        ):
            with self.assertRaises(helper.DeviceDisconnected):
                phone.wait_for_usb(timeout=2)
        self.assertEqual(len(run.call_args_list), 2)
        for call in run.call_args_list:
            self.assertEqual(call.args, ("adb", "-s", "original", "get-state"))

    def test_adb_missing_device_is_recognized_as_usb_loss(self):
        result = CompletedProcess(["adb"], 1, b"", b"adb.exe: device 'original' not found")
        with patch.object(helper.subprocess, "run", return_value=result):
            with self.assertRaises(helper.DeviceDisconnected):
                helper.run_command("adb", "-s", "original", "get-state")

    def test_settled_stage_is_not_executed(self):
        with patch.object(helper, "finish_task") as finish:
            helper.run_stages(self.phone, self.trace, "page", [self.vision], {"page"})
        finish.assert_not_called()

    def test_reward_dismissal_reuses_the_confirmed_task_page(self):
        image = object()
        self.vision.identify.return_value = ("reward", None)
        self.vision.match.return_value = (100, 200, 120, 220)
        with patch.object(helper, "wait_for_page", return_value=(image, "task", None)) as wait:
            dismissed, page = helper.dismiss_reward(self.phone, self.vision, self.trace)
            result = helper.find_task(self.phone, self.vision, self.trace, page=page)
        self.assertEqual(dismissed, "reward")
        self.assertIs(result, image)
        self.phone.tap.assert_called_once_with((110, 210))
        self.phone.frame.assert_called_once()
        wait.assert_called_once()

    def test_shared_plus_three_dialog_does_not_complete_an_unverified_task(self):
        vision = MagicMock(task=helper.TASKS[1])
        page = (object(), "list", None)
        with (
            patch.object(helper, "dismiss_reward", return_value=("reward_final", page)),
            patch.object(helper, "find_task", return_value=None) as find,
        ):
            helper.finish_task(self.phone, vision, self.trace, allow_missing=True)
        find.assert_called_once_with(self.phone, vision, self.trace, allow_missing=True, page=page)
        self.trace.record.assert_called_once_with("task_not_listed", task="goods")

    def test_promo_restart_checks_progress_before_another_tap(self):
        vision = MagicMock(task=helper.TASKS[2])
        frame = object()
        with (
            patch.object(helper, "dismiss_reward", return_value=(None, (frame, "task", None))),
            patch.object(helper, "find_task", return_value=frame),
            patch.object(helper, "read_progress", side_effect=[(0, 1), (0, 1), (0, 1), (1, 1)]),
            patch.object(helper, "timed_count", return_value=10),
            patch.object(helper, "browse_one", return_value=None) as browse,
        ):
            helper.finish_task(self.phone, vision, self.trace, allow_missing=True)
        self.phone.restart_mgame.assert_called_once_with(vision, self.trace)
        self.assertEqual(browse.call_count, 2)
        self.trace.record.assert_any_call("task_complete", task="promo", completed=1, total=1)

    def test_full_run_defers_unsettled_promo_until_after_timed_stages(self):
        visions = [MagicMock(task=task) for task in helper.TASKS]
        with (
            patch.object(helper, "finish_task", side_effect=[None, None, helper.RoundUnsettled("promo"), None]) as finish,
            patch.object(helper, "finish_games"),
            patch.object(helper, "finish_browse10"),
            patch.object(helper, "finish_app_jump") as app_jump,
        ):
            helper.run_stages(self.phone, self.trace, "all", visions, set())
        started = [call.kwargs["stage"] for call in self.trace.record.call_args_list if call.args[0] == "stage_started"]
        self.assertEqual(started, ["page", "goods", "promo", "games", "browse10", "play20", "listen30", "watch60", "promo"])
        app_jump.assert_not_called()
        self.assertFalse(finish.call_args.kwargs["allow_missing"])
        self.trace.record.assert_any_call("stage_deferred", reason="promo")
        self.trace.record.assert_called_with("all_stages_finished")

    def test_full_run_handles_independent_stages_before_handing_off_verification(self):
        calls = []

        def finish_timed(phone, vision, trace, rule):
            calls.append(rule)
            if rule == "browse10":
                raise helper.VerificationRequired("manual verification")

        with patch.object(helper, "finish_games"), patch.object(helper, "finish_browse10", side_effect=finish_timed):
            with self.assertRaises(helper.VerificationRequired):
                helper.run_stages(self.phone, self.trace, "all", [], set())

        self.assertEqual(calls, ["browse10", "play20", "listen30", "watch60", "browse10"])
        self.phone.recover_mgame_main.assert_called_once()
        self.assertFalse(any(call.args[0] == "all_stages_finished" for call in self.trace.record.call_args_list))

    def test_consecutive_webviews_are_closed(self):
        self.phone.frame.side_effect = ["store", "error", "reward"]
        with (
            patch.object(helper, "time", Clock()),
            patch.object(helper, "observe", side_effect=[("unknown", None), ("unknown", None), ("reward", None)]),
            patch.object(helper, "close_ad_webview", return_value=True) as close,
        ):
            result = helper.wait_for_page(self.phone, self.vision, {"reward"}, trace=self.trace, settling=True)
        self.assertEqual(result[1], "reward")
        self.assertEqual(close.call_count, 2)

    def test_app_jump_requires_ten_uninterrupted_foreground_seconds(self):
        clock = Clock()
        package = "com.UCMobile"

        def foreground():
            if self.phone.adb_call.called or 9 <= clock.now < 10:
                return helper.MGAME
            return package

        self.phone.foreground.side_effect = foreground
        with patch.object(helper, "time", clock):
            self.assertTrue(helper.visit_app_jump(self.phone, self.trace, package))

        self.assertGreaterEqual(clock.now, 20)
        self.assertLess(clock.now, 21)
        self.phone.adb_call.assert_called_once_with("shell", "input", "keyevent", "KEYCODE_BACK")
        self.trace.record.assert_any_call("app_jump_visit_elapsed", package=package, seconds=10)
        self.trace.record.assert_any_call("app_jump_returned_by_back", package=package)
        self.phone.return_to_mgame.assert_not_called()

    def test_app_jump_returns_without_counting_an_app_lock(self):
        package = "com.UCMobile"
        self.phone.foreground.return_value = helper.APP_LOCK

        with patch.object(helper, "time", Clock()):
            self.assertFalse(helper.visit_app_jump(self.phone, self.trace, package))

        self.trace.record.assert_called_once_with("app_jump_app_lock", package=package)
        self.phone.adb_call.assert_not_called()
        self.phone.return_to_mgame.assert_called_once()

    def test_app_jump_rechecks_before_stopping_for_a_delayed_claim(self):
        clock = Clock()
        package = "com.baidu.BaiduMap"
        image = object()
        title = (190, 900, 490, 935)
        button = (900, 2200, 1010, 2260)
        go = {package: ("百度地图", "go", button)}
        claim = {package: ("百度地图", "claim", button)}
        self.phone.adb_call.return_value = f"package:{package}\n".encode()

        with (
            patch.object(helper, "time", clock),
            patch.object(helper, "game60_panel", return_value=image),
            patch.object(helper, "find_app_jump_list", return_value=(image, title)),
            patch.object(helper, "app_jump_title", return_value=title),
            patch.object(helper, "app_jump_progress", side_effect=[7, 7, 7, 8]),
            patch.object(helper, "app_jump_cards", side_effect=[go, go, go, claim, claim]),
            patch.object(helper, "visit_app_jump", return_value=True) as visit,
        ):
            helper.finish_app_jump(self.phone, self.vision, self.trace, set())

        visit.assert_called_once_with(self.phone, self.trace, package)
        self.assertEqual(self.phone.tap.call_count, 2)
        self.trace.record.assert_any_call("app_jump_settlement_wait", package=package, progress=7)
        self.trace.record.assert_any_call("app_jump_result", package=package, before=7, after=8)

    def test_app_jump_restarts_mgame_once_and_claims_without_revisiting(self):
        package = "com.baidu.BaiduMap"
        image = object()
        title = (190, 900, 490, 935)
        button = (900, 2200, 1010, 2260)
        go = {package: ("百度地图", "go", button)}
        claim = {package: ("百度地图", "claim", button)}
        self.phone.adb_call.return_value = f"package:{package}\n".encode()

        with (
            patch.object(helper, "time", Clock()),
            patch.object(helper, "Vision", return_value=self.vision),
            patch.object(helper, "game60_panel", return_value=image),
            patch.object(helper, "find_app_jump_list", return_value=(image, title)),
            patch.object(helper, "app_jump_title", return_value=title),
            patch.object(helper, "app_jump_progress", side_effect=[7, 7, 7, 7, 7, 8]),
            patch.object(helper, "app_jump_cards", side_effect=[go, go, go, go, go, claim, claim]),
            patch.object(helper, "visit_app_jump", return_value=True) as visit,
            patch.object(helper, "finish_task") as daily,
        ):
            helper.run_stages(self.phone, self.trace, "appjump", [], set())

        self.phone.restart_mgame.assert_called_once_with(self.vision, self.trace)
        visit.assert_called_once_with(self.phone, self.trace, package)
        self.assertEqual(self.phone.tap.call_count, 2)
        daily.assert_not_called()
        self.trace.record.assert_any_call("app_jump_result", package=package, before=7, after=8)

    def test_app_jump_does_not_restart_mgame_a_second_time(self):
        with patch.object(helper, "finish_app_jump", side_effect=helper.RoundUnsettled("pending")) as finish:
            with self.assertRaises(helper.RoundUnsettled):
                helper.run_stages(self.phone, self.trace, "appjump", [], set())

        self.phone.restart_mgame.assert_called_once()
        self.assertEqual(finish.call_count, 2)

    def test_webview_closure_has_a_limit(self):
        self.phone.frame.return_value = "unknown"
        with (
            patch.object(helper, "time", Clock()),
            patch.object(helper, "observe", return_value=("unknown", None)),
            patch.object(helper, "close_ad_webview", return_value=True) as close,
        ):
            with self.assertRaises(helper.LandingUnconfirmed):
                helper.wait_for_page(self.phone, self.vision, {"reward"}, timeout=1, trace=self.trace, settling=True)
        self.assertEqual(close.call_count, 3)

    def test_rotated_live_capture_retries_before_matching(self):
        phone = helper.Phone.__new__(helper.Phone)
        phone.adb_call = MagicMock(return_value=b"png")
        phone.require_mgame = MagicMock()
        landscape = helper.np.zeros((1080, 2400, 3), dtype=helper.np.uint8)
        portrait = helper.np.zeros((2400, 1080, 3), dtype=helper.np.uint8)
        with patch.object(helper, "time", Clock()), patch.object(helper, "decode", side_effect=[landscape, portrait]):
            self.assertIs(phone.frame(), portrait)
        self.assertEqual(phone.adb_call.call_count, 2)
        with patch.object(helper, "decode", return_value=landscape):
            self.assertIs(phone.capture(calibrated=False), landscape)

    def test_reward_count_rechecks_a_number_missing_digits(self):
        image = object()
        capture = MagicMock(return_value=image)
        with patch.object(helper, "timed_count", side_effect=[4, 294]) as count:
            self.assertEqual(helper.confirm_reward_count(capture, self.trace, "listen", 292, 2, 10), (image, 294))
        capture.assert_called_once()
        self.assertTrue(count.call_args.kwargs["alternate"])

    def test_late_qqlive_return_button_recovers_reward_wait(self):
        self.phone.frame.side_effect = [helper.ForegroundChanged(), helper.ForegroundChanged(), "reward"]
        self.phone.foreground.return_value = "com.tencent.qqlive"
        with (
            patch.object(helper, "time", Clock()),
            patch.object(helper, "observe", return_value=("reward", None)),
            patch.object(helper, "tap_qqlive_return", side_effect=[False, True]) as tap,
        ):
            _, state, _ = helper.wait_for_page(self.phone, self.vision, {"reward"}, trace=self.trace, settling=True)
        self.assertEqual(state, "reward")
        self.assertEqual(tap.call_count, 2)

    def test_qqlive_return_rechecks_the_activity_before_tapping(self):
        package = "com.tencent.qqlive"
        self.phone.foreground_component.side_effect = [[package, "previous"], [package, "next"]]
        self.vision.match.return_value = (10, 20, 30, 40)
        self.assertFalse(helper.tap_qqlive_return(self.phone, self.vision, self.trace))
        self.phone.adb_call.assert_not_called()

    def test_visible_entry_failure_ends_wait_after_a_short_grace_period(self):
        clock = Clock()
        self.vision.match.return_value = (1, 1, 2, 2)
        with (
            patch.object(helper, "time", clock),
            patch.object(helper, "observe", return_value=("task", None)),
        ):
            with self.assertRaisesRegex(helper.LandingUnconfirmed, "操作失败"):
                helper.wait_for_page(self.phone, self.vision, {"ad"}, timeout=18)
        self.assertGreaterEqual(clock.now, 3)
        self.assertLess(clock.now, 4)
        self.vision.match.assert_called_once()

    def test_browse10_ignores_the_repeating_promotion_row(self):
        def match(image, name, region):
            if name == helper.TASKS[2].title and region[1] == 916:
                return (180, 920, 450, 975)
            if name == "go":
                return (900, region[1], 980, region[3])

        self.vision.match.side_effect = match
        with patch.object(helper, "rule_positions", return_value=[1000, 1200]):
            rows = list(helper.browse10_rows(self.vision, object()))
        self.assertEqual([row[0] for row in rows], [1200])

    def test_opening_timed_task_list_handles_an_existing_reward(self):
        image = object()
        with (
            patch.object(helper, "wait_for_page", side_effect=[(object(), "coins", None), (object(), "reward", None)]) as wait,
            patch.object(helper, "tap_verified") as tap,
            patch.object(helper, "dismiss_reward", return_value=("reward", (image, "list", None))) as dismiss,
        ):
            result = helper.game60_panel(self.phone, self.vision, self.trace)
        self.assertIs(result, image)
        tap.assert_called_once_with(self.phone, self.vision, self.trace, "coins")
        dismiss.assert_called_once()
        self.assertIn("reward", wait.call_args.args[2])

    def test_fullscreen_ad_button_requires_both_instruction_and_label(self):
        image = helper.np.zeros((2400, 1080, 3), dtype=helper.np.uint8)
        with patch.object(helper, "read_ocr", side_effect=["浏览 6 秒领取奖励", "立即前往"]):
            button = helper.promo_fullscreen_button(image, helper.PROMO_SIGMOB_AD, self.trace)
        self.assertEqual(helper.center(button), (530, 1524))
        with patch.object(helper, "read_ocr", side_effect=["浏览 6 秒领取奖励", "立即安装"]):
            self.assertIsNone(helper.promo_fullscreen_button(image, helper.PROMO_SIGMOB_AD, self.trace))

    def test_douyin_portrait_variant_uses_the_verified_open_button(self):
        image = helper.np.zeros((2400, 1080, 3), dtype=helper.np.uint8)
        labels = ["开 App 体验 15 秒", "", "抖 音", "点击打开或下载第三方应用"]
        with patch.object(helper, "read_ocr", side_effect=labels):
            button = helper.promo_fullscreen_button(image, helper.PROMO_PORTRAIT_AD, self.trace)
        self.assertEqual(button, helper.DOUYIN_OPEN_BUTTON)

    def test_video_end_card_uses_the_central_launch_button(self):
        image = helper.np.zeros((2400, 1080, 3), dtype=helper.np.uint8)
        with patch.object(helper, "read_ocr", side_effect=["", "百度地图\n启动\n重新播放"]):
            button = helper.promo_fullscreen_button(image, helper.PROMO_VIDEO_AD, self.trace)
        self.assertEqual(button, helper.PROMO_VIDEO_END_BUTTON)

    def test_game_screenshot_time_counts_toward_wait(self):
        clock = Clock()
        self.phone.foreground_component.return_value = [helper.MGAME, helper.GAME_ACTIVITY]
        self.phone.home.return_value = "launcher"
        self.phone.foreground.return_value = "launcher"
        self.trace.snapshot.side_effect = lambda *args, **kwargs: setattr(clock, "now", clock.now + 3)
        with patch.object(helper, "time", clock), patch.object(helper, "return_game60_panel"):
            helper.play_game60(self.phone, self.vision, self.trace, seconds=62)
        self.assertEqual(clock.waits, [59])
        self.phone.return_to_mgame.assert_called_once()

    def test_media_screenshot_time_counts_toward_wait(self):
        clock = Clock()
        self.phone.foreground_component.return_value = [helper.MGAME, helper.TIMED_PLAYERS["listen30"][0]]
        self.trace.snapshot.side_effect = lambda *args, **kwargs: setattr(clock, "now", clock.now + 3)
        with (
            patch.object(helper, "time", clock),
            patch.object(helper, "start_novel_audio"),
            patch.object(helper, "leave_novel_audio"),
            patch.object(helper, "leave_drama"),
            patch.object(helper, "game60_panel"),
        ):
            helper.timed_media(self.phone, self.vision, self.trace, "listen30")
        self.assertEqual(clock.now, 32)
        self.assertTrue(any(call.args[0] == "timed_media_completed" for call in self.trace.record.call_args_list))

    def test_media_interruption_cannot_report_completion(self):
        clock = Clock()
        self.phone.foreground_component.side_effect = [[helper.MGAME, helper.TIMED_PLAYERS["watch60"][0]], [helper.MGAME, "changed"]]
        # Even if saving a screenshot takes the whole wait, verify the final component.
        self.trace.snapshot.side_effect = lambda *args, **kwargs: setattr(clock, "now", 63)
        with patch.object(helper, "time", clock):
            with self.assertRaises(helper.LandingUnconfirmed):
                helper.timed_media(self.phone, self.vision, self.trace, "watch60")
        self.trace.record.assert_any_call("timed_media_interrupted", rule="watch60", component=[helper.MGAME, "changed"])
        self.assertFalse(any(call.args[0] == "timed_media_completed" for call in self.trace.record.call_args_list))
        self.phone.back.assert_not_called()


if __name__ == "__main__":
    unittest.main()
