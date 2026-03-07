from common.baseline import SERVER_PORT, get_host
from traditional.server.server import TraditionalServer

if __name__ == "__main__":
    TraditionalServer(host = get_host(),port=SERVER_PORT).run()
    