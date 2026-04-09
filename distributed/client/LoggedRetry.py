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
            # Extract query parameters from URL
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            
            # Extract request_id (parse_qs returns a list, take the first element)
            req_ids = query_params.get('request_id')
            if req_ids:
                req_id = req_ids[0]
                # Add req_id to the set, duplicates are automatically handled by the set
                self.client.retried_requests.add(req_id)
                
                retry_count = len(self.history) + 1
                self.client.logger.warning(f"[REQ-{req_id}] Automatic retry triggered (attempt {retry_count})")
            
        return super().increment(method, url, *args, **kwargs)