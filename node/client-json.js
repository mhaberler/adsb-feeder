const WebSocket = require('ws');

var args = process.argv.slice(2);
console.log('args: ', args);

var uri = args[0];
var bbox = {
    "min_latitude": 46,
    "max_latitude": 47,
    "min_longitude": 13,
    "max_longitude": 17,
    "min_altitude": -100,
    "max_altitude": 10000000
};

const ws = new WebSocket(uri, ['adsb-json']);

ws.on('open', function open() {
    console.log("connection opened, sending bounding box");
    // example for dynamically changing the bbox of the updates
    ws.send(JSON.stringify(bbox));
});

ws.on('message', function incoming(data) {
    const msg = JSON.parse(data);
    console.log(msg);
});
