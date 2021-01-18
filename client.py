import sys
import requests
import geobuf
import base64
import json
import argparse

from twisted.internet import reactor, ssl

from autobahn.twisted.websocket import WebSocketClientFactory, \
    WebSocketClientProtocol, \
    connectWS

# sent as dynamic bbox update once session established
bbox = {
    "min_latitude": 46,
    "max_latitude": 47,
    "min_longitude": 16,
    "max_longitude": 17,
    "min_altitude": -100,
    "max_altitude": 10000000
};


class ADSBFeederClientProtocol(WebSocketClientProtocol):

    def sendBBox(self):
        print("updating bounding box")
        self.sendMessage(json.dumps(bbox).encode())

    def onOpen(self):
        print(f"WebSocket connection opened")

        # after 2 seconds send a bbox update
        reactor.callLater(2, self.sendBBox)

    def onConnect(self, response):
        print(f"onConnect response={response} proto={self.websocket_protocol_in_use}")

    def onMessage(self, payload, isBinary):
        if self.websocket_protocol_in_use == 'adsb-geobuf':
            msg = geobuf.decode(payload)
            print(f"geobuf received: isBinary={isBinary} msg={msg}")

        if self.websocket_protocol_in_use == 'adsb-json':
            msg = json.loads(payload.decode('utf8'))
            print(f"json received: isBinary={isBinary} msg={msg}")

    def onClose(self, wasClean, code, reason):
        print(f"WebSocket connection closed: wasClean={wasClean} code={code} reason={reason}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='adsb-feeder Python client example',
        add_help=True)

    parser.add_argument('--url',
                        dest='url',
                        action='store',
                        type=str,
                        required=True,
                        help='Websocket URL to connect to, like "wss://example.com/adsb/"')

    parser.add_argument('--protocol',
                        dest='protocol',
                        action='store',
                        type=str,
                        default=None,
                        help="Websocket subprotocol to use, like adsb-geobuf or adsb-json")

    args = parser.parse_args()
    headers = []
    factory = WebSocketClientFactory(args.url,  headers=headers)
    factory.protocol = ADSBFeederClientProtocol
    if args.protocol:
        factory.protocols = [args.protocol]
    else:
        factory.protocols = ['adsb-geobuf', 'adsb-json']

    if factory.isSecure:
        contextFactory = ssl.ClientContextFactory()
    else:
        contextFactory = None

    connectWS(factory, contextFactory)
    reactor.run()
