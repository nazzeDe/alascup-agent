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


class TestFeatureTimeTrackerStartComplete:
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
    def test_module_level_complete_unstarted_raises(self):
        from src.tools.feature_time_tracker import complete_feature

        with pytest.raises(ValueError, match="has not been started"):
            complete_feature("never-started-feature")
