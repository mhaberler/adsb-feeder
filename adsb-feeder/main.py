"""

run like:

python main.py --upstream  tcp:193.228.47.165:30003 --upstream   data.adsbhub.org:5002
  --reporter tcp:1234
  --websocket ws://127.0.0.1:9000
  -l DEBUG
  --downstream tcp:1079
  --permanent

clients:

  telnet 127.0.0.1 1079
  websocat ws://user:pass@127.0.0.1:9000
  websocat "ws://user:pass@127.0.0.1:9000?min_latitude=45&max_latitude=47&min_longitude=15&max_longitude=17"

update bounding box by sending a JSON bounding box like so:

select an area:
{  "min_latitude": 42,  "max_latitude": 43, "min_longitude": 15,  "max_longitude":  17,  "min_altitude": -100, "max_altitude": 10000000}
full feed:
{  "min_latitude": -90, "max_latitude": 90, "min_longitude": -180, "max_longitude":  180,  "min_altitude": -100, "max_altitude": 10000000}

profile:
 python -m cProfile -o profile.stats main.py --upstream  tcp:193.228.47.165:30003  --reporter tcp:1234 --websocket ws://127.0.0.1:9000 -l IN --downstream tcp:1079 --permanent  --upstream   tcp:data.adsbhub.org:5002

"""

from twisted.internet import reactor
from twisted.internet.protocol import ReconnectingClientFactory, Protocol, Factory
from twisted.protocols import basic
from twisted.internet.endpoints import clientFromString, serverFromString
from twisted.internet.task import LoopingCall
from twisted.application.internet import ClientService, backoffPolicy, StreamServerEndpointService
from twisted.application import internet, service
from twisted.python.log import PythonLoggingObserver, ILogObserver, startLogging #WithObserver
from twisted.web.server import Site
from twisted.web.resource import Resource

from autobahn.twisted.websocket import WebSocketServerFactory, \
    WebSocketServerProtocol, \
    listenWS
from autobahn.websocket.types import ConnectionDeny

import sys
import os
import logging
import logging.handlers
import syslog
import jsonschema

import orjson
import argparse
from datetime import datetime, timedelta, timezone
import base64
from collections import Counter
import geojson
import geobuf

import observer
import boundingbox
from jwt import InvalidAudienceError, ExpiredSignatureError, InvalidSignatureError, PyJWTError
from jwtauth import *

appName = "Feeder"
facility = syslog.LOG_LOCAL1
PING_EVERY = 30 # secs for now

def within(lat, lon, alt, bbox):
    if lat < bbox.min_latitude:
        return False
    if lat > bbox.max_latitude:
        return False
    if lon < bbox.min_longitude:
        return False
    if lon > bbox.max_longitude:
        return False
    if alt < bbox.min_altitude:
        return False
    if alt > bbox.max_altitude:
        return False
    return True


class UpstreamProtocol(basic.LineOnlyReceiver):
    delimiter = b'\r\n'

    def __init__(self):
        self.feedstats = Counter(bytes=0, lines=0)

    def connectionMade(self):
        log.debug(f'[x] upstream connection established to'
                  f' {self.transport.getPeer()}')
        self.factory.upstreams.add(self)
        self.factory.countConnect(self.transport.getPeer().host)

    def connectionLost(self, reason):
        log.debug(f'[ ] upstream connection to {self.transport.getPeer()}lost:'
                     f' {reason.value}')
        self.factory.upstreams.discard(self)

    def lineReceived(self, line):
        #log.debug(f'[x] line {line} received from upstream  {self.transport.getPeer()}')
        self.feedstats['lines'] += 1
        self.feedstats['bytes'] += len(line)
        self.factory.flight_observer.parse(line.decode())


class UpstreamClientFactory(Factory):

    upstreams = set()
    connects = dict()

    def __init__(self, protocol, flight_observer, permanent, parent, typus):
        self.protocol = protocol
        self.clients = set()
        self.flight_observer = flight_observer
        self.permanent = permanent
        self.parent = parent
        self.typus = typus

    def countConnect(self, host):
        if not host in self.connects:
            self.connects[host] = Counter(connects=0)
        self.connects[host]['connects'] += 1

    def registerClient(self, client):
        log.debug(f"registerClient {type(client)}")
        self.clients.add(client)
        if not self.permanent and len(self.clients) == 1:
            self.parent.startService()

    def unregisterClient(self, client):
        log.debug(f"unregisterClient {type(client)}")
        self.clients.discard(client)
        if not self.permanent and len(self.clients) == 0:
            self.parent.stopService()


def client_updater(flight_observer, feeder_factory):

    if not feeder_factory.clients:
        return

    # BATCH THIS!!
    obs = flight_observer.getObservations()

    for icao, o in obs.items():
        if not o.isUpdated():
            continue
        if not o.isPresentable():
            continue

        lat = o.getLat()
        lon = o.getLon()
        alt = o.getAltitude()

        r = orjson.dumps(o.__geo_interface__, option=orjson.OPT_APPEND_NEWLINE)
        pbf = geobuf.encode(o.__geo_interface__)

        for client in feeder_factory.clients:
            if within(lat, lon, alt, client.bbox):
                if isinstance(client, Downstream):
                    client.transport.write(r)
                if isinstance(client, WSServerProtocol):
                    if not client.usr:
                        continue
                    if client.proto == 'adsb-geobuf':
                        client.sendMessage(pbf, True)
                    if client.proto == 'adsb-json':
                        client.sendMessage(r, False)
        o.resetUpdated()


class WSServerProtocol(WebSocketServerProtocol):

    def onConnecting(self, transport_details):
        logging.info(f"WebSocket connecting: {transport_details}")
        self.last_heard = datetime.utcnow().timestamp()


    def doPing(self):
        if self.run:
            self.sendPing()
            #self.factory.pingsSent[self.peer] += 1
            log.debug(f"Ping sent to {self.peer}")
            reactor.callLater(PING_EVERY, self.doPing)

    def onPong(self, payload):
        #self.factory.pongsReceived[self.peer] += 1
        self.last_heard = datetime.utcnow().timestamp()
        log.debug(f"Pong received from {self.peer}")


    def onConnect(self, request):
        log.debug(f"Client connecting: {request.peer} version {request.version}")
        log.debug(f"headers: {request.headers}")
        log.debug(f"path: {request.path}")
        log.debug(f"params: {request.params}")
        log.debug(f"protocols: {request.protocols}")
        log.debug(f"extensions: {request.extensions}")
        self.peer = request.peer
        self.usr = None  # until after jwt decoded

        self.bbox = boundingbox.BoundingBox()
        self.bbox.fromParams(request.params)
        self.geobuf = 'options' in request.params and 'geobuf' in request.params['options']
        self.forwarded_for = request.headers.get('x-forwarded-for', '')
        self.host = request.headers.get('host', '')

        self.proto = None
        # server-side preference of subprotocol
        for p in self.factory._subprotocols:
            if p in request.protocols:
                self.proto = p
                break

        if not self.proto:
            raise ConnectionDeny(ConnectionDeny.BAD_REQUEST)

        self.user_agent = request.headers.get('user-agent',"")

        log.debug(f"chosen protocol {self.proto} for {self.forwarded_for} via {self.peer} ua={self.user_agent}")

        if 'token' in request.params:
            try:
                for token in request.params['token']:
                    obj = self.factory.jwt_auth.decodeToken(token)
                    log.debug(f"token={obj} from {self.forwarded_for}")
                    self.usr = obj['usr']
                    finish = min(datetime.utcnow().timestamp() +
                                 obj['dur'], obj['exp'])
                    close_in = round(finish - datetime.utcnow().timestamp())
                    reactor.callLater(int(close_in), self.sessionExpired)
                    log.debug(
                        f"session expires in {close_in} seconds - {datetime.fromtimestamp(finish)}")
                    break

            except PyJWTError as e:
                log.error(f"JWTError  {e}")
                raise ConnectionDeny(1066)


        else:
            log.info(f"no token passed in URI by {self.forwarded_for} via {request.peer}")
            raise ConnectionDeny(1066)

        # accept the WebSocket connection, speaking subprotocol `proto`
        # and setting HTTP headers `headers`
        # return (proto, headers)
        return self.proto

    def sessionExpired(self):
        self.factory.feeder_factory.unregisterClient(self)
        log.debug(f"token validity time exceeded, closing {self.forwarded_for} via {self.peer}")
        self.sendClose()

    def onOpen(self):
        log.debug(f"connection open to {self.forwarded_for} via {self.peer}")
        self.run = True
        self.factory.feeder_factory.registerClient(self)
        self.doPing()


    def onMessage(self, payload, isBinary):
        if isBinary:
            log.debug(f"Binary message received: {len(payload)} bytes")
        else:
            log.debug(f"Text message received: {payload.decode('utf8')}")

        (success, bbox, response) = self.factory.bbox_validator.validate_str(payload)
        if not success:
            log.info(f'{self.peer} bbox update failed: {response}')
            self.sendMessage(orjson.dumps(
                response, option=orjson.OPT_APPEND_NEWLINE), isBinary)
        else:
            log.debug(f'{self.peer} updated bbox: {bbox}')
            self.bbox = bbox

    def onClose(self, wasClean, code, reason):
        log.debug(
            f"WebSocket connection closed by {self.forwarded_for} via {self.peer}: wasClean={wasClean} code={code} reason={reason}")
        self.run = False
        self.factory.feeder_factory.unregisterClient(self)


class WSServerFactory(WebSocketServerFactory):

    protocol = WSServerProtocol
    _subprotocols = ['adsb-geobuf', 'adsb-json']


class Downstream(Protocol):

    def __init__(self):
        self.bbox = boundingbox.BoundingBox()

    def connectionMade(self):
        log.debug(
            f'[x] downstream connection established from {self.transport.getPeer()}')
        self.factory.feeder_factory.registerClient(self)

    def connectionLost(self, reason):
        log.debug(
            f'[ ] downstream disconnected: {self.transport.getPeer()} {reason.value}')
        self.factory.feeder_factory.unregisterClient(self)

    def dataReceived(self, data):
        log.debug(
            f'==> received {data} from downstream  {self.transport.getPeer()}')
        (success, bbox, response) = self.factory.bbox_validator.validate_str(data)
        if not success:
            self.transport.write(json.dumps(response).encode("utf8"))
        else:
            log.debug(f'{self.transport.getPeer()} updated bbox: {bbox}')
            self.bbox = bbox


class DownstreamFactory(Factory):

    protocol = Downstream


def setup_logging(level, facility, appName):
    ##twisted_logging = PythonLoggingObserver('twisted')
    #twisted_logging.start()

    #logging.getLogger("twisted").setLevel(level)

    startLogging(sys.stdout) #twisted_logging)

    global log
    log = logging.getLogger(appName)
    log.setLevel(level)

    fmt = logging.Formatter('%(asctime)s.%(msecs)03d %(levelname)-3s '
                            '%(filename)-12s%(lineno)3d %(message)s')
    stderrHandler = logging.StreamHandler(sys.stderr)
    stderrHandler.setLevel(level)
    stderrHandler.setFormatter(fmt)

    if sys.platform.startswith('linux'):
        syslogHandler = logging.handlers.SysLogHandler(
            address='/dev/log', facility=facility)
    elif sys.platform.startswith('darwin'):
        syslogHandler = logging.handlers.SysLogHandler(
            address='/var/run/syslog', facility=facility)
    else:
        syslogHandler = logging.handlers.SysLogHandler(facility=facility)
    syslogHandler.setFormatter(fmt)
    syslogHandler.setLevel(level)

    log.addHandler(syslogHandler)
    log.addHandler(stderrHandler)
    #startLoggingWithObserver(twisted_logging)

def xxxsetup_logging(level, facility, appName):
    global log
    log = logging.getLogger(appName)
    log.setLevel(level)
    startLogging(sys.stdout) #twisted_logging)


class StateResource(Resource):

    def __init__(self, flight_observer, feeder_factory,
                 downstream_factory, websocket_factory):
        self.observer = flight_observer
        self.feeder_factory = feeder_factory
        self.downstream_factory = downstream_factory
        self.websocket_factory = websocket_factory

    def render_GET(self, request):
        self.now = datetime.utcnow().timestamp()
        log.debug(f'render_GET request={request} args={request.args}')
        rates, distribution, observations, span = self.observer.stats()
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        distribution_table = ""
        for k, v in distribution:
            distribution_table += f"\t\t<tr><td>{k}</td><td>{v}%</td></tr>\n"

        upstreams = """
<table>
<tr>
    <th>feed</th>
    <th>(re)connects)</th>
    <th>msgs received</th>
    <th>total byes</th>
    <th>typus</th>
</tr>"""
        for u in self.feeder_factory.upstreams:
            upstreams += (

#FIXME          self.feeder_factory.connects[host]['connects'] += 1
                f"<tr><td>{u.transport.getPeer()}</td>"
                f"<td>{u.feedstats['connects']}</td>"
                f"<td>{u.feedstats['lines']}</td>"
                f"<td>{u.feedstats['bytes']}</td>"
                f"<td>{u.factory.typus}</td>"
                f"</tr>\n"
            )
        upstreams += "</table>"

        tcp_clients = ""
        ws_clients = ""

        for client in self.feeder_factory.clients:
            if isinstance(client, Downstream):
                tcp_clients += f"\t\t<tr><td>{client.transport.getPeer()}</td><td>{client.bbox}</td></tr>\n"

            if isinstance(client, WSServerProtocol):
                ws_clients += (
                    f"\t\t<tr><td>{client.peer}</td>"
                    f"<td>{client.bbox}</td>"
                    f"<td>{client.usr}</td>"
                    f"<td>{client.forwarded_for}</td>"
                    f"<td>{client.user_agent}</td></tr>\n"
                    f"<td>{self.now - client.last_heard:.1f} s ago</td></tr>\n"
                    f"</tr>\n"
                )
        aircraft = """
<H2>Aircraft observed</H2>
<table>
<tr>
    <th>icao</th>
    <th>callsign</th>
    <th>sqawk</th>
    <th>lat</th>
    <th>lon</th>
    <th>altitude</th>
    <th>speed</th>
    <th>vspeed</th>
    <th>heading</th>
</tr>"""
        for icao, o in observations.items():
            #log.debug(f'icao={icao} o={o}')
            if not o.isPresentable():
                continue
            d = o.as_dict()
            aircraft += "<tr>"
            for k in ["icao24", "callsign", "squawk", "lat", "lon", "altitude", "speed", "vspeed", "heading"]:
                aircraft += f'<td>{d[k]}</td>'
            aircraft += "</tr>"
        aircraft += "</table>"

        response = f"""\
<HTML>
    <HEAD><TITLE>ADS-B feed statistics</title></head>
    <BODY>
    <H1>ADS-B feed statistics as of {datetime.today()}</H1>
    <H2>observation statistics (last {span} seconds)</H2>
    <table>
    <tr>
        <td>currently observing:</td>
        <td>{rates['observations']} aircraft</td>
    </tr>
    <tr>
        <td>observation rate:</td>
        <td>{rates['observation_rate']}/s</td>
    </tr>
    </table>
    <H2>SBS-1 Message type distribution</H2>
    <table>
       {distribution_table}
    </table>
    <H2>ADS-B feeders</H2>
   {upstreams}
    <H2>Websocket clients</H2>
    <table>
       {ws_clients}
    </table>
    <H2>TCP clients</H2>
    <table>
       {tcp_clients}
    </table>
    {aircraft}
    </body>
</html>"""
        return response.encode('utf-8')


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def main():
    parser = argparse.ArgumentParser(
        description='merge several SBS-1 feeds to downstream clients',
        add_help=True)

    parser.add_argument('--upstream',
                        dest='upstreams',
                        action='append',
                        type=str,
                        default=[],
                        help='upstream outgoing connect definition like tcp:1.2.3.4:30003')

    parser.add_argument('--upstream-server',
                        dest='upstreamServer',
                        action='store',
                        type=str,
                        help='upstream listen definition like tcp:30003:interface=192.168.1.1')

    parser.add_argument('--downstream',
                        dest='downstream',
                        action='store',
                        type=str,
                        default=None,
                        help='downstream listen definition like tcp:1079:interface=192.168.1.1')

    parser.add_argument('--websocket',
                        dest='websocket',
                        action='store',
                        type=str,
                        default=None,
                        help='websocket listen definition like ws://127.0.0.1:1080')

    parser.add_argument('--reporter',
                        dest='reporter',
                        action='store',
                        type=str,
                        default=None,
                        help='HTTP status report listen definition like tcp:1080')

    parser.add_argument("--permanent", type=str2bool, nargs='?',
                        const=True, default=False,
                        help="always keep feeder connections open.")

    parser.add_argument('-l', '--log',
                        help="set the logging level. Arguments:  DEBUG, INFO, WARNING, ERROR, CRITICAL",
                        choices=['DEBUG', 'INFO',
                                 'WARNING', 'ERROR', 'CRITICAL'],
                        dest="logLevel")

    parser.add_argument('-D', '--debug-parser',
                        help="debug the inner loop - lots of log output!",
                        nargs='?',
                        const=True,
                        default=False,
                        dest="debugParser")

    args = parser.parse_args()

    level = logging.WARNING
    if args.logLevel:
        level = getattr(logging, args.logLevel)

    setup_logging(level, facility, appName)

    log.debug("{appName} starting up")
    observer.trace_parser = args.debugParser
    observer.log = log
    boundingbox.log = log
    jwt_authenticator = JWTAuthenticator(issuer="urn:mah.priv.at",
                                         audience=WSServerFactory._subprotocols,
                                         algorithm="HS256")

    feeders = service.MultiService()

    bbox_validator = boundingbox.BBoxValidator()
    websocket_factory = None

    if args.websocket:
        websocket_factory = WSServerFactory(args.websocket)
        websocket_factory.bbox_validator = bbox_validator
        websocket_factory.jwt_auth = jwt_authenticator


    downstream_factory = None
    if args.downstream:
        downstream_factory = DownstreamFactory()
        downstream_factory.bbox_validator = bbox_validator
        downstream_server = serverFromString(reactor, args.downstream)

    flight_observer = observer.FlightObserver()

    retryPolicy = backoffPolicy(initialDelay=0.5, factor = 2.71828, maxDelay=20)

    upstream_server_factory = None
    if args.upstreamServer:
        upstream_server_factory = UpstreamClientFactory(UpstreamProtocol, flight_observer, True, feeders, "listener")
        upstream_server_endpoint = serverFromString(reactor, args.upstreamServer)
        feeder_server = StreamServerEndpointService(upstream_server_endpoint, upstream_server_factory)
        feeder_server.setServiceParent(feeders)

    feeder_factory = UpstreamClientFactory(UpstreamProtocol, flight_observer, args.permanent, feeders, "outbound connector")
    for dest in args.upstreams:
        feeder_endpoint = clientFromString(reactor, dest)
        feeder = ClientService(feeder_endpoint, feeder_factory, retryPolicy=retryPolicy)
        feeder.setServiceParent(feeders)

    if args.downstream:
        downstream_factory.feeders = feeders
        downstream_factory.feeder_factory = feeder_factory
        downstream_server.listen(downstream_factory)

#    if args.upstreamServer:

    if args.websocket:
        websocket_factory.feeder_factory = feeder_factory
        listenWS(websocket_factory)

    if args.permanent:
        feeders.startService()

    if args.reporter:
        root = Resource()
        root.putChild(b"", StateResource(flight_observer, feeder_factory,
                                         downstream_factory, websocket_factory))
        webserver = serverFromString(reactor, args.reporter).listen(Site(root))

    lc = LoopingCall(client_updater,
                     flight_observer, feeder_factory)
    lc.start(0.3)

    reactor.run()


if __name__ == '__main__':
    main()
