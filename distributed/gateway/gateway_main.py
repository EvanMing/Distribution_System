from common.baseline import GATEWAY_PORT, LOCAL_HOST, SERVER_PORT, get_host
from distributed.gateway.gateway import DistributedGateway

if __name__ == "__main__":
    gateway = DistributedGateway(gateway_host=get_host(),gateway_port=GATEWAY_PORT,server_url=f'http://{LOCAL_HOST}:{SERVER_PORT}').run()
