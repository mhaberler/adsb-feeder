import sys
import requests
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


    def onMessage(self, payload, isBinary):
        if not isBinary:
            msg = json.loads(payload.decode('utf8'))
            print(f"message received: {msg}")

    def onClose(self, wasClean, code, reason):
        print(f"WebSocket connection closed: {reason}")

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
    parser.add_argument('--user',
                        dest='user',
                        action='store',
                        type=str,
                        required=True,
                        help='username')
    parser.add_argument('--password',
                        dest='password',
                        action='store',
                        type=str,
                        required=True,
                        help='password')

    args = parser.parse_args()

    # use Basic Authentication
    usrPass = f"{args.user}:{args.password}"
    b64val = base64.b64encode(usrPass.encode()).decode("utf8")
    headers={"Authorization": f"Basic {b64val}"}

    # create a WS server factory with our protocol
    ##
    factory = WebSocketClientFactory(args.url,  headers=headers)
    factory.protocol = ADSBFeederClientProtocol

    # SSL client context: default
    ##
    if factory.isSecure:
        contextFactory = ssl.ClientContextFactory()
    else:
        contextFactory = None

    connectWS(factory, contextFactory)
    reactor.run()
