import unittest

from realtime_audio_translator.performance import END_TO_END_P50, END_TO_END_P95, END_TO_END_P99, FIRST_SUBTITLE_P50, FIRST_SUBTITLE_P95, FIRST_SUBTITLE_P95_BUDGET_SECONDS, FIRST_SUBTITLE_P99, TTS_QUEUE_DEPTH, LatencyWindow, metric_value


class PerformanceTests(unittest.TestCase):
    def test_latency_window_reports_stress_percentiles_with_bounded_memory(self):
        window = LatencyWindow(maxlen=1000)
        for value in range(1, 10001):
            window.add(value / 1000)

        self.assertEqual(len(window.samples), 1000)
        self.assertEqual(window.snapshot(), {END_TO_END_P50: 9.5, END_TO_END_P95: 9.95, END_TO_END_P99: 9.99})

    def test_metric_value_accepts_only_defined_numeric_metrics(self):
        self.assertEqual(metric_value({END_TO_END_P95: "2.5"}, END_TO_END_P95), 2.5)
        self.assertEqual(metric_value({TTS_QUEUE_DEPTH: 1}, TTS_QUEUE_DEPTH), 1.0)
        self.assertIsNone(metric_value({END_TO_END_P95: "bad"}, END_TO_END_P95))
        with self.assertRaises(KeyError):
            metric_value({}, "last_ambiguous_latency")

    def test_first_subtitle_p95_stays_within_three_second_budget(self):
        window = LatencyWindow()
        for value in [2.35] * 18 + [2.80] * 2:
            window.add(value)

        metrics = window.snapshot((FIRST_SUBTITLE_P50, FIRST_SUBTITLE_P95, FIRST_SUBTITLE_P99))
        self.assertEqual(metrics[FIRST_SUBTITLE_P95], 2.80)
        self.assertLessEqual(metrics[FIRST_SUBTITLE_P95], FIRST_SUBTITLE_P95_BUDGET_SECONDS)


if __name__ == "__main__":
    unittest.main()
