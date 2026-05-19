"""审批桥接器：记录 request_id → chat_id 映射。

Query._run_loop 在 interrupt 时调用 create() 建立映射；
HTTP 审批端点通过 get_chat_id() 查找所属会话，构建 Query 并调用 resume()。
"""


class ApprovalBridge:
    def __init__(self):
        self._chat_ids: dict[str, str] = {}

    def create(self, request_id: str, chat_id: str) -> None:
        self._chat_ids[request_id] = chat_id

    def get_chat_id(self, request_id: str) -> str | None:
        return self._chat_ids.get(request_id)
