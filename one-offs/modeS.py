import pyModeS as pms
from pyModeS.extra.tcpclient import TcpClient

# define your custom class by extending the TcpClient
#   - implement your handle_messages() methods
class ADSBClient(TcpClient):
    def __init__(self, host, port, rawtype):
        super(ADSBClient, self).__init__(host, port, rawtype)

    def handle_messages(self, messages):
        for msg, ts in messages:
            print("--", msg)
            if len(msg) != 28:  # wrong data length
                continue

            df = pms.df(msg)

            if df != 17:  # not ADSB
                continue

            if pms.crc(msg) !=0:  # CRC fail
                continue

            icao = pms.adsb.icao(msg)
            tc = pms.adsb.typecode(msg)

            # TODO: write you magic code here
            print(ts, icao, tc, msg)

# run new client, change the host, port, and rawtype if needed
client = ADSBClient(host='172.16.0.169', port=30002, rawtype='raw')
client.run()
