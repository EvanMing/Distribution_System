from traditional.gateway.gateway import TraditionalGateway

# 传统模式-网关启动入口
if __name__ == "__main__":
    def get_gateway_host():
        import socket
        print('hostname:',socket.gethostname())
        if "laptop" in socket.gethostname().lower():
            return "127.0.0.1" 
        return '0.0.0.0'
    gateway = TraditionalGateway(gateway_host=get_gateway_host(),gateway_port=8080,server_url='http://127.0.0.1:8000')
    gateway.run()
