import time
import threading

import pytest

pytestmark = pytest.mark.unit


class TestNormalizeFeatureName:
    def test_strips_whitespace(self):
        from src.observability.timing import _normalize_feature_name

        assert _normalize_feature_name("  my-feature  ") == "my-feature"

    def test_empty_raises(self):
        from src.observability.timing import _normalize_feature_name

        with pytest.raises(ValueError, match="must not be empty"):
            _normalize_feature_name("")

    def test_whitespace_only_raises(self):
        from src.observability.timing import _normalize_feature_name

        with pytest.raises(ValueError, match="must not be empty"):
            _normalize_feature_name("   ")


class TestFeatureTimeTrackerStartComplete:
    def test_complete_unstarted_raises(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        with pytest.raises(ValueError, match="has not been started"):
            tracker.complete_feature("nonexistent")

    def test_complete_removes_active_start(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op")
        # Second complete should fail
        with pytest.raises(ValueError, match="has not been started"):
            tracker.complete_feature("op")

    def test_complete_empty_status_defaults_to_success(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op", status="")
        records = tracker._records
        assert records[-1].status == "success"

    def test_complete_whitespace_status_defaults_to_success(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op", status="   ")
        assert tracker._records[-1].status == "success"

    def test_complete_custom_status_preserved(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        tracker.start_feature("op")
        tracker.complete_feature("op", status="failed")
        assert tracker._records[-1].status == "failed"


class TestFeatureTimeTrackerSummarize:
    def test_single_feature_multiple_runs_averages(self):
        from src.observability.timing import FeatureTimeTracker

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
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker()
        errors = []

        def worker(name):
            try:
                tracker.start_feature(name)
                time.sleep(0.001)
                tracker.complete_feature(name)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"op-{i}",)) for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        summary = tracker.summarize_feature_durations()
        assert len(summary) == 20


class TestModuleLevelSingleton:
    def test_module_level_complete_unstarted_raises(self):
        from src.observability.timing import complete_feature

        with pytest.raises(ValueError, match="has not been started"):
            complete_feature("never-started-feature")


class TestProfilingEnabled:
    """Profiling path: checkpoint, report, _features, _feature_starts."""

    def test_checkpoint_records_when_enabled(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=True)
        tracker.checkpoint("step-1")
        tracker.checkpoint("step-2")

        assert len(tracker._checkpoints) == 2
        assert tracker._checkpoints[0].label == "step-1"

    def test_checkpoint_noop_when_disabled(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=False)
        tracker.checkpoint("step-1")

        assert len(tracker._checkpoints) == 0

    def test_report_returns_string_when_enabled(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=True)
        tracker.checkpoint("agent_step_run")

        report = tracker.report()
        assert report is not None
        assert "agent_step_run" in report

    def test_report_returns_none_when_disabled_and_no_features(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=False)
        assert tracker.report() is None

    def test_feature_timing_populates_both_records_and_features_when_enabled(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=True)
        tracker.start_feature("op")
        tracker.complete_feature("op", status="ok")

        assert len(tracker._records) == 1
        assert tracker._records[0].feature_name == "op"
        assert len(tracker._features) == 1
        assert tracker._features[0].name == "op"

    def test_feature_timing_only_records_when_disabled(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=False)
        tracker.start_feature("op")
        tracker.complete_feature("op")

        assert len(tracker._records) == 1
        assert len(tracker._features) == 0
        assert len(tracker._feature_starts) == 0

    def test_feature_starts_cleaned_up_after_complete(self):
        from src.observability.timing import FeatureTimeTracker

        tracker = FeatureTimeTracker(profile_enabled=True)
        tracker.start_feature("op")
        assert "op" in tracker._feature_starts
        tracker.complete_feature("op")
        assert "op" not in tracker._feature_starts


class TestWriteProfile:
    """write_profile honours module-level _enabled flag."""

    def test_write_profile_noop_when_disabled(self, monkeypatch):
        monkeypatch.setenv("ALASCUP_PROFILE", "0")
        from src.observability.timing import write_profile, profiler_enabled

        if profiler_enabled():
            pytest.skip("ALASCUP_PROFILE is set in this environment")

        # Must not raise, even with no file path configured.
        write_profile("should be ignored")
