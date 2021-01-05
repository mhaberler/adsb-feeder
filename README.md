# adsb-feeder
ADS-B feed aggregator and websocket server

connects to any number of SBS1 feeds like dump1090:30003 or data.adsbhub.org:5002

usage:

```
python adsb-feeder/main.py <options>

Options are:
connect to an SBS1 feed like dump1090 port 30003:
--upstream tcp:<ip4 or hostname>:30003

connect to the ADSBHub.org feed aggregator:
--upstream tcp:data.adsbhub.org:5002

provide an unauthenticated raw TCP stream of JSON-formatted updates on port 1079:
--downstream tcp:1079

Provide a websockets server at port 9000:
--websocket ws://127.0.0.1:9000

Provide an HTML feed status page on localhost:9001
--reporter tcp:9001:interface=127.0.0.1

Connect permanently to all upstream feeds (default is to connect only if clients present):
--permanent

set the log level:
--log INFO  (or DEBUG...)
```
## server setup

I run reporter and websockets services behind an nginx SSL proxy, see nginx-fragments.conf .

for a systemd service see adsbhub.service .

## leaflet demo

This shows dynamically updated markers for each tracked plane. The feed's bounding box
is dynamically updated on pan/zoom/drag for minimizing traffic.


You need to edit leaflet/index.html for host and credentials first.

## python client

This example will connect and initially receive a full feed of updates. After
two seconds, the bounding box is updated by the client so only updates in the area
of interest are sent.
```
$ python  client.py --user <user> --password <password> --url wss://<host>/adsb/
```
## node client (see https://github.com/websockets/ws)
demonstrates reading and decoding a stream of updates from adsb-feed

call like:

```
$ npm install ws
$ node client.js  'wss://<user>:<password>@<host>/adsb/'
```
## websocat client (see https://github.com/vi/websocat)

call like:

```
$ websocat 'wss://<user>:<password>@<host>/adsb/'
```
## wscat client (see https://www.npmjs.com/package/wscat)

```
$ wscat -c 'wss://<user>:<password>@<host>/adsb/'
```

## filtering by bounding box on connect

This can be achieved by URL params like so:

```
$ websocat 'wss://<user>:<password>@<host>/adsb/?min_latitude=46&max_latitude=47&min_longitude=15&max_longitude=16&min_altitude=2000&max_altitude=4000'
```

Any subsequent updates sent by the client override the initial bounding box.

## Credentials

Drop me a mail for a test user/password credential.

## Credits
The SBS-1 parser is mostly based on Jon Kanflo's https://github.com/kanflo/ADS-B-funhouse - thanks!
