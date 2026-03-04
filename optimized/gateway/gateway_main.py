from optimized.gateway.gateway import OptimizedGateway

if __name__ == "__main__":
    def get_gateway_host():
        import socket
        print('hostname:',socket.gethostname())
        if "compute" in socket.gethostname():
            return '0.0.0.0'
        return "127.0.0.1" 
    gateway = OptimizedGateway(gateway_host=get_gateway_host(),gateway_port=8080,server_url='http://127.0.0.1:8000')
    gateway.run()
