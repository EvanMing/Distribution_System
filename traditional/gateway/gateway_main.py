from common.baseline import GATEWAY_PORT, LOCAL_HOST, SERVER_PORT, get_host
from traditional.gateway.gateway import TraditionalGateway

if __name__ == "__main__":
    TraditionalGateway(gateway_host=get_host(),gateway_port=GATEWAY_PORT,server_url=f'http://{LOCAL_HOST}:{SERVER_PORT}').run()
