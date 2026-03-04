# 优化版本-服务端启动入口
from optimized.server.server import OptimizedServer
if __name__ == "__main__":
    server = OptimizedServer(host = '0.0.0.0',port=8000)
    server.run()
