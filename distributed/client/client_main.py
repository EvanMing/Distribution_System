from common.baseline import GATEWAY_PORT, get_host
from distributed.client.client import DistributedClient


if __name__ == "__main__": 
    DistributedClient(gateway_host=get_host(), gateway_port=GATEWAY_PORT).run()
    