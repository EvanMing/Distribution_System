from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class RequestResult:
    is_success: bool
    req_size: int
    res_size: int
    latency: float
    json_res: Optional[Dict[str, Any]]
    req_id: str
    task_id: str
    task_type: str

    @property
    def server_note(self) -> Optional[str]:
        return self.json_res.get('server_note') if self.json_res else None

    @property
    def gateway_note(self) -> Optional[str]:
        return self.json_res.get('gateway_note') if self.json_res else None