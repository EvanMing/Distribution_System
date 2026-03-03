from optimized.gateway.gateway import OptimizedGateway

# 优化版本-网关启动入口
if __name__ == "__main__":
    gateway = OptimizedGateway(gateway_host='127.0.0.1',gateway_port=8080)
    gateway.run()