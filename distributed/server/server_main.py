from common.baseline import SERVER_PORT, get_host
from distributed.server.server import DistributedServer

if __name__ == "__main__":
    DistributedServer(host = get_host(),port=SERVER_PORT).run()
