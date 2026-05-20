import time
import threading

import pytest

pytestmark = pytest.mark.unit


class TestNormalizeFeatureName:
    def test_strips_whitespace(self):
        from src.tools.feature_time_tracker import _normalize_feature_name

        assert _normalize_feature_name("  my-feature  ") == "my-feature"

    def test_empty_raises(self):
        from src.tools.feature_time_tracker import _normalize_feature_name

        with pytest.raises(ValueError, match="must not be empty"):
            _normalize_feature_name("")

    def test_whitespace_only_raises(self):
        from src.tools.feature_time_tracker import _normalize_feature_name

        with pytest.raises(ValueError, match="must not be empty"):
            _normalize_feature_name("   ")


class TestFormatDurationMs:
    def test_formats_two_decimals(self):
        from src.tools.feature_time_tracker import _format_duration_ms

        assert _format_duration_ms(123.456) == "123.46"


class TestFeatureDurationRecord:
    def test_frozen_and_fields(self):
        from src.tools.feature_time_tracker import FeatureDurationRecord

        r = FeatureDurationRecord(feature_name="test", duration_ms=100.0, status="success")
        assert r.feature_name == "test"
        assert r.duration_ms == 100.0
        assert r.status == "success"

        with pytest.raises(Exception):
            r.duration_ms = 200.0


class TestFeatureTimeTrackerStartComplete:
    def test_start_and_complete_returns_duration(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("my-op")
        time.sleep(0.01)
        duration = tracker.complete_feature("my-op")
        assert duration > 0

    def test_start_and_complete_with_different_spacing(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("  my-op  ")
        duration = tracker.complete_feature("my-op")
        assert duration >= 0

    def test_complete_unstarted_raises(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        with pytest.raises(ValueError, match="has not been started"):
            tracker.complete_feature("nonexistent")

    def test_complete_removes_active_start(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op")
        # Second complete should fail
        with pytest.raises(ValueError, match="has not been started"):
            tracker.complete_feature("op")

    def test_complete_empty_status_defaults_to_success(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op", status="")
        records = tracker._records
        assert records[-1].status == "success"

    def test_complete_whitespace_status_defaults_to_success(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op", status="   ")
        assert tracker._records[-1].status == "success"

    def test_complete_custom_status_preserved(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op", status="failed")
        assert tracker._records[-1].status == "failed"


class TestFeatureTimeTrackerSummarize:
    def test_empty_summary(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        assert tracker.summarize_feature_durations() == {}

    def test_single_feature_single_run(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op")
        summary = tracker.summarize_feature_durations()
        assert "op" in summary
        assert summary["op"] > 0

    def test_single_feature_multiple_runs_averages(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        time.sleep(0.01)
        d1 = tracker.complete_feature("op")
        tracker.start_feature("op")
        time.sleep(0.03)
        d2 = tracker.complete_feature("op")

        summary = tracker.summarize_feature_durations()
        avg = summary["op"]
        assert avg == pytest.approx((d1 + d2) / 2, rel=0.2)

    def test_multiple_features_separate(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op-a")
        tracker.complete_feature("op-a")
        tracker.start_feature("op-b")
        tracker.complete_feature("op-b")

        summary = tracker.summarize_feature_durations()
        assert "op-a" in summary
        assert "op-b" in summary


class TestFeatureTimeTrackerThreadSafety:
    def test_concurrent_start_complete_no_corruption(self):
        from src.tools.feature_time_tracker import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        errors = []

        def worker(name):
            try:
                tracker.start_feature(name)
                time.sleep(0.001)
                tracker.complete_feature(name)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"op-{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        summary = tracker.summarize_feature_durations()
        assert len(summary) == 20


class TestModuleLevelSingleton:
    def test_get_tracker_returns_same_instance(self):
        from src.tools.feature_time_tracker import get_tracker

        t1 = get_tracker()
        t2 = get_tracker()
        assert t1 is t2

    def test_module_level_convenience_functions(self):
        from src.tools.feature_time_tracker import (
            complete_feature,
            start_feature,
            summarize_feature_durations,
        )

        start_feature("mod-op")
        duration = complete_feature("mod-op")
        assert duration > 0

        summary = summarize_feature_durations()
        assert "mod-op" in summary

    def test_module_level_complete_unstarted_raises(self):
        from src.tools.feature_time_tracker import complete_feature

        with pytest.raises(ValueError, match="has not been started"):
            complete_feature("never-started-feature")
