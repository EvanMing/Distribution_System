from common.baseline import SERVER_PORT, get_host
from optimized.server.server import OptimizedServer

if __name__ == "__main__":
    OptimizedServer(host = get_host(),port=SERVER_PORT).run()
