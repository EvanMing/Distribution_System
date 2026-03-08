from common.baseline import BACKUP_SERVER_PORT, GATEWAY_PORT, LOCAL_HOST, SERVER_PORT, get_host
from distributed.gateway.gateway import DistributedGateway

if __name__ == "__main__":
    gateway = DistributedGateway(
        gateway_host=get_host(),
        gateway_port=GATEWAY_PORT,
        server_url=f'http://{LOCAL_HOST}:{SERVER_PORT}',
        backup_server_url=f'http://{LOCAL_HOST}:{BACKUP_SERVER_PORT}' # 注入备用节点
    ).run()