from common.baseline import BACKUP_SERVER_PORT,  get_host
from distributed.server.server_backup import DistributedBackupServer

if __name__ == "__main__":
    DistributedBackupServer(host = get_host(),port=BACKUP_SERVER_PORT).run()
