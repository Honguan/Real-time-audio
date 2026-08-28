import unittest

from realtime_audio_translator.performance import END_TO_END_P50, END_TO_END_P95, END_TO_END_P99, LatencyWindow, metric_value


class PerformanceTests(unittest.TestCase):
    def test_latency_window_reports_stress_percentiles_with_bounded_memory(self):
        window = LatencyWindow(maxlen=1000)
        for value in range(1, 10001):
            window.add(value / 1000)

        self.assertEqual(len(window.samples), 1000)
        self.assertEqual(window.snapshot(), {END_TO_END_P50: 9.5, END_TO_END_P95: 9.95, END_TO_END_P99: 9.99})

    def test_metric_value_accepts_only_defined_numeric_metrics(self):
        self.assertEqual(metric_value({END_TO_END_P95: "2.5"}, END_TO_END_P95), 2.5)
        self.assertIsNone(metric_value({END_TO_END_P95: "bad"}, END_TO_END_P95))
        with self.assertRaises(KeyError):
            metric_value({}, "last_ambiguous_latency")


if __name__ == "__main__":
    unittest.main()
