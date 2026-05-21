import pytest

pytestmark = pytest.mark.unit


class TestMcpClientApiError:
    def test_instantiation_with_all_args(self):
        from src.error import McpClientApiError

        err = McpClientApiError(
            error_code="TOOL_EXECUTION_FAILED",
            message="Something went wrong",
            status_code=500,
            detail="Permission denied",
        )

        assert err.error_code == "TOOL_EXECUTION_FAILED"
        assert err.message == "Something went wrong"
        assert err.status_code == 500
        assert err.detail == "Permission denied"
        assert str(err) == "Something went wrong"

    def test_default_status_code_and_detail(self):
        from src.error import McpClientApiError

        err = McpClientApiError(
            error_code="NOT_FOUND",
            message="Resource missing",
        )

        assert err.error_code == "NOT_FOUND"
        assert err.message == "Resource missing"
        assert err.status_code == 400
        assert err.detail == ""

    def test_is_exception_subclass(self):
        from src.error import McpClientApiError

        err = McpClientApiError("E001", "test")
        assert isinstance(err, Exception)

    def test_can_be_raised_and_caught(self):
        from src.error import McpClientApiError

        with pytest.raises(McpClientApiError, match="Something went wrong"):
            raise McpClientApiError(
                error_code="ERR",
                message="Something went wrong",
            )
