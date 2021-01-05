# adsb-feeder
ADS-B feed aggregator and websocket server

connects to any number of feeds like dump1090:30003 or data.adsbhub.org:5002

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

## credentials

Drop me a mail for a test user/password credential.

## Credits
The SBS-1 parser is mostly based on Jon Kanflo's https://github.com/kanflo/ADS-B-funhouse - thanks!
