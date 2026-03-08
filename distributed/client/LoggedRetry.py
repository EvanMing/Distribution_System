from urllib3 import Retry
from urllib.parse import urlparse, parse_qs



class LoggedRetry(Retry):
    def __init__(self, *args, **kwargs):
        self.client = kwargs.pop('client', None)
        super().__init__(*args, **kwargs)

    def new(self, **kw):
        kw['client'] = self.client
        return super().new(**kw)

    def increment(self, method, url, *args, **kwargs):
        if self.client and url:
            # 从 URL 中提取查询参数
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            
            # 提取 request_id（parse_qs 返回的是列表，取第一个）
            req_ids = query_params.get('request_id')
            if req_ids:
                req_id = req_ids[0]
                # 将 req_id 加入集合，集合会自动去重
                self.client.retried_requests.add(req_id)
                
                retry_count = len(self.history) + 1
                self.client.logger.warning(f"[REQ-{req_id}] 触发自动重试 (第{retry_count}次)")
            
        return super().increment(method, url, *args, **kwargs)