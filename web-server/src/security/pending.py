"""审批桥接器：记录 request_id → session_id 映射。

Query._run_loop 在 interrupt 时调用 create() 建立映射；
HTTP 审批端点通过 get_session_id() 查找所属会话，构建 Query 并调用 resume()。
"""


class ApprovalBridge:
    def __init__(self):
        self._session_ids: dict[str, str] = {}

    def create(self, request_id: str, session_id: str) -> None:
        self._session_ids[request_id] = session_id

    def get_session_id(self, request_id: str) -> str | None:
        return self._session_ids.get(request_id)
