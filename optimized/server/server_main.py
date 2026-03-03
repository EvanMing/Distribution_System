# 优化版本-服务端启动入口
from optimized.server.server import OptimizedServer
if __name__ == "__main__":
    server = OptimizedServer(host = '127.0.0.1',port=8000)
    server.run()