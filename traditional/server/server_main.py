from traditional.server.server import TraditionalServer

if __name__ == "__main__":
    def get_server_host():
        import socket
        print('hostname:',socket.gethostname())
        if "laptop" in socket.gethostname().lower():
            return "127.0.0.1" 
        return '0.0.0.0'
    server = TraditionalServer(host = get_server_host(),port=8000)
    server.run()