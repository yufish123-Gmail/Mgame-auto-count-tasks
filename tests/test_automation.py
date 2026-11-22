import unittest
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

    def test_settled_stage_is_not_executed(self):
        with patch.object(helper, "finish_task") as finish:
            helper.run_stages(self.phone, self.trace, "page", [self.vision], {"page"})
        finish.assert_not_called()

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
            with self.assertRaises(helper.Stop):
                helper.timed_media(self.phone, self.vision, self.trace, "watch60")
        self.assertFalse(any(call.args[0] == "timed_media_completed" for call in self.trace.record.call_args_list))
        self.phone.back.assert_not_called()


if __name__ == "__main__":
    unittest.main()
