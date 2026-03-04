from optimized.gateway.gateway import OptimizedGateway

# 优化版本-网关启动入口
if __name__ == "__main__":
    gateway = OptimizedGateway(gateway_host='0.0.0.0',gateway_port=8080,server_url='http://172.31.20.170:8000')
    gateway.run()
