from traditional.gateway.gateway import TraditionalGateway

# 传统模式-网关启动入口
if __name__ == "__main__":
    gateway = TraditionalGateway()
    gateway.run()