from common.baseline import GATEWAY_PORT, LOCAL_HOST, SERVER_PORT, get_host
from optimized.gateway.gateway import OptimizedGateway

if __name__ == "__main__":
    gateway = OptimizedGateway(gateway_host=get_host(),gateway_port=GATEWAY_PORT,server_url=f'http://{LOCAL_HOST}:{SERVER_PORT}').run()
    gateway.run()
