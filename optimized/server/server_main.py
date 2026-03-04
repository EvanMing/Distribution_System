from optimized.server.server import OptimizedServer

if __name__ == "__main__":
    def get_server_host():
        import socket
        print('hostname:',socket.gethostname())
        if "compute" in socket.gethostname():
            return '0.0.0.0'
        return "127.0.0.1" 
    server = OptimizedServer(host = get_server_host(),port=8000)
    server.run()
