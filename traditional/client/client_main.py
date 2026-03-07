from common.baseline import GATEWAY_PORT, get_host
from traditional.client.client import TraditionalClient


if __name__ == "__main__": 
    TraditionalClient(gateway_host=get_host(), gateway_port=GATEWAY_PORT).run()