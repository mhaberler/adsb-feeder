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
from twisted.application.internet import ClientService
from twisted.application import internet, service
from twisted.python.log import PythonLoggingObserver, ILogObserver
from twisted.web.server import Site
from twisted.web.resource import Resource

from autobahn.twisted.websocket import WebSocketServerFactory, \
    WebSocketServerProtocol, \
    listenWS
from autobahn.websocket.types import ConnectionDeny

import sys
import logging
import logging.handlers
import syslog
import jsonschema
#import json
import orjson
import argparse
import datetime
import base64
from collections import Counter
import geojson
import geobuf

import observer
import boundingbox

appName = "Feeder"
facility = syslog.LOG_LOCAL1

def within(lat, lon, alt, bbox):
    if lat < bbox.min_latitude: return False
    if lat > bbox.max_latitude: return False
    if lon < bbox.min_longitude: return False
    if lon > bbox.max_longitude: return False
    if alt < bbox.min_altitude: return False
    if alt > bbox.max_altitude: return False
    return True

class UpstreamProtocol(basic.LineOnlyReceiver):
    delimiter = b'\n'

    def __init__(self):
        self.feedstats = Counter(connects=0, lines=0)

    def connectionMade(self):
        log.debug(f'[x] upstream connection established to'
                  f' {self.transport.getPeer()}')
        self.feedstats['connects'] += 1
        self.factory.resetDelay()
        self.factory.upstreams.add(self)

    def connectionLost(self, reason):
        log.debug(f'[ ] upstream connection to {self.transport.getPeer()}lost:'
                  f' {reason.value}')
        self.factory.upstreams.discard(self)

    def lineReceived(self, line):
        self.feedstats['lines'] += 1
        # typeof(m) = Observation()
        m = self.factory.flight_observer.parse(line.decode())


class UpstreamFactory(ReconnectingClientFactory):

    protocol = UpstreamProtocol
    initialDelay = 2
    maxDelay = 30
    upstreams = set()


    def __init__(self, flight_observer, permanent, parent):
        self.downstream_clients = set()
        self.websocket_clients = set()
        self.flight_observer = flight_observer
        self.permanent = permanent
        self.parent = parent

    def client_added(self):
        if self.permanent:
            return
        if len(self.downstream_clients) + len(self.websocket_clients) == 1:
            self.parent.startService()

    def client_removed(self):
        if self.permanent:
            return
        if len(self.downstream_clients) + len(self.websocket_clients) == 0:
            self.parent.stopService()

    def add_ws_client(self, client):
        self.websocket_clients.add(client)
        self.client_added()

    def del_ws_client(self, client):
        self.websocket_clients.discard(client)
        self.client_removed()

    def add_ds_client(self, client):
        self.downstream_clients.add(client)
        self.client_added()

    def del_ds_client(self, client):
        self.downstream_clients.discard(client)
        self.client_removed()

def client_updater(flight_observer, feeder_factory):

    if not feeder_factory.downstream_clients and not feeder_factory.websocket_clients:
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
        pbf = geobuf.encode(o.__geo_interface__, 0,3)

        for c in feeder_factory.downstream_clients:
            if within(lat, lon, alt, c.bbox):
                c.transport.write(r)
        for ws in feeder_factory.websocket_clients:
            if within(lat, lon, alt, ws.bbox):
                if ws.geobuf:
                    ws.sendMessage(pbf, True)
                else:
                    ws.sendMessage(r, False)
        o.resetUpdated()


class WSServerProtocol(WebSocketServerProtocol):


    def onConnect(self, request):
        log.debug(f"Client connecting: {request.peer} version {request.version}")
        log.debug(f"headers: {request.headers}")
        log.debug(f"path: {request.path}")
        log.debug(f"params: {request.params}")
        log.debug(f"protocols: {request.protocols}")
        log.debug(f"extensions: {request.extensions}")
        self.peer = request.peer

        self.bbox = boundingbox.BoundingBox()
        self.bbox.fromParams(request.params)
        self.geobuf = 'options' in request.params and 'geobuf' in request.params['options']

        if 'authorization' not in request.headers:
            raise ConnectionDeny( 4000, u'Missing authorization')

        self.forwarded_for = request.headers.get('x-forwarded-for','')
        log.debug(f"forwarded for: {self.forwarded_for}")

        authheader = request.headers['authorization']
        log.debug(f"authheader {authheader}")
        elements = authheader.split(" ")
        if elements[0].lower() == 'basic':
            user, _ = base64.b64decode(elements[1]).split(b":")
            self.user = user.decode("utf8")
            log.debug(f"user {self.user}")
        else:
            raise ConnectionDeny(4001, u'basic authorization required')


    def onOpen(self):
        log.debug(f"WebSocket connection open.")
        self.factory.feeder_factory.add_ws_client(self)

    def onMessage(self, payload, isBinary):
        if isBinary:
            log.debug(f"Binary message received: {len(payload)} bytes")
        else:
            log.debug(f"Text message received: {payload.decode('utf8')}")

        (success, bbox, response) = self.factory.bbox_validator.validate_str(payload)
        if not success:
            log.info(f'{self.peer} bbox update failed: {response}')

            #self.sendMessage(json.dumps(response).encode("utf8"), isBinary)
            #r = orjson.dumps(response, option=orjson.OPT_APPEND_NEWLINE)

            self.sendMessage(json.dumps(response).encode("utf8"), isBinary)
        else:
            log.debug(f'{self.peer} updated bbox: {bbox}')
            self.bbox = bbox


    def onClose(self, wasClean, code, reason):
        log.debug(f"WebSocket connection closed by {self.peer}: wasClean={wasClean} code={code} reason={reason}")
        self.factory.feeder_factory.del_ws_client(self)

class WSServerFactory(WebSocketServerFactory):

    protocol = WSServerProtocol
    websocket_clients = set()


class Downstream(Protocol):

    def __init__(self):
        self.bbox = boundingbox.BoundingBox()

    def connectionMade(self):
        log.debug(
            f'[x] downstream connection established from {self.transport.getPeer()}')
        self.factory.feeder_factory.add_ds_client(self)

    def connectionLost(self, reason):
        log.debug(
            f'[ ] downstream disconnected: {self.transport.getPeer()} {reason.value}')
        self.factory.feeder_factory.del_ds_client(self)

    def dataReceived(self, data):
        log.debug(f'==> received {data} from downstream  {self.transport.getPeer()}')
        (success, bbox, response) = self.factory.bbox_validator.validate_str(data)
        if not success:
            self.transport.write(json.dumps(response).encode("utf8"))
        else:
            log.debug(f'{self.transport.getPeer()} updated bbox: {bbox}')
            self.bbox = bbox

class DownstreamFactory(Factory):

    protocol = Downstream


def setup_logging(level, facility, appName):
    twisted_logging = PythonLoggingObserver('twisted')
    twisted_logging.start()
    logging.getLogger("twisted").setLevel(level)

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


class StateResource(Resource):

    def __init__(self, flight_observer,feeder_factory,
                 downstream_factory, websocket_factory):
        self.observer = flight_observer
        self.feeder_factory = feeder_factory
        self.downstream_factory = downstream_factory
        self.websocket_factory = websocket_factory

    def render_GET(self, request):
        log.debug(f'render_GET request={request} args={request.args}')
        rates, distribution, observations, span = self.observer.stats()
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        distribution_table = ""
        for k,v in distribution:
            distribution_table += f"\t\t<tr><td>{k}</td><td>{v}%</td></tr>\n"

        upstreams = """
<table>
<tr>
    <th>feed</th>
    <th>(re)connects)</th>
    <th>msgs received</th>
</tr>"""
        for u in self.feeder_factory.upstreams:
            upstreams += (
                f"<tr><td>{u.transport.getPeer()}</td>"
                f"<td>{u.feedstats['connects']}</td>"
                f"<td>{u.feedstats['lines']}</td></tr>\n"
            )
        upstreams += "</table>"

        tcp_clients = ""
        for d in self.feeder_factory.downstream_clients:
            tcp_clients += f"\t\t<tr><td>{d.transport.getPeer()}</td><td>{d.bbox}</td></tr>\n"

        ws_clients = ""
        for w in self.feeder_factory.websocket_clients:
            ws_clients += f"\t\t<tr><td>{w.peer}</td><td>{w.bbox}</td><td>{w.user}</td><td>{w.forwarded_for}</td></tr>\n"

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
        for icao,o in observations.items():
            #log.debug(f'icao={icao} o={o}')
            if not o.isPresentable():
                continue
            d = o.as_dict()
            aircraft += "<tr>"
            for k in ["icao24","callsign", "squawk", "lat","lon","altitude","speed","vspeed","heading"]:
                aircraft += f'<td>{d[k]}</td>'
            aircraft += "</tr>"
        aircraft += "</table>"

        response = f"""\
<HTML>
    <HEAD><TITLE>ADS-B feed statistics</title></head>
    <BODY>
    <H1>ADS-B feed statistics as of {datetime.datetime.today()}</H1>
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
                        required=True,
                        help='upstream definition like tcp:1.2.3.4:30003')

    parser.add_argument('--downstream',
                        dest='downstream',
                        action='store',
                        type=str,
                        default=None,
                        help='downstream listen definition like tcp:1079')

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
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        dest="logLevel")


    args = parser.parse_args()

    level = logging.WARNING
    if args.logLevel:
        level = getattr(logging, args.logLevel)

    setup_logging(level, facility, appName)
    observer.log = log
    boundingbox.log = log

    bbox_validator  = boundingbox.BBoxValidator()
    websocket_factory = None

    if args.websocket:
        websocket_factory = WSServerFactory(args.websocket)
        websocket_factory.bbox_validator = bbox_validator

    downstream_factory = None
    if args.downstream:
        downstream_factory = DownstreamFactory()
        downstream_factory.bbox_validator = bbox_validator
        downstream_server = serverFromString(reactor, args.downstream)

    flight_observer = observer.FlightObserver()

    feeders = service.MultiService()
    feeder_factory = UpstreamFactory(flight_observer, args.permanent, feeders)

    for dest in args.upstreams:
        feeder_endpoint = clientFromString(reactor, dest)
        feeder = ClientService(feeder_endpoint, feeder_factory)
        feeder.setServiceParent(feeders)

    if args.downstream:
        downstream_factory.feeders = feeders
        downstream_factory.feeder_factory = feeder_factory
        downstream_server.listen(downstream_factory)

    if args.websocket:
        websocket_factory.feeder_factory = feeder_factory
        listenWS(websocket_factory)

    if args.permanent:
        feeders.startService()

    if args.reporter:
        root = Resource()
        root.putChild(b"", StateResource(flight_observer,feeder_factory,
                                         downstream_factory, websocket_factory))
        webserver = serverFromString(reactor, args.reporter).listen(Site(root))


    lc = LoopingCall(client_updater,
                     flight_observer, feeder_factory)
    lc.start(0.3)


    reactor.run()


if __name__ == '__main__':
    main()
