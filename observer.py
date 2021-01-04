#!/usr/bin/env python3
#
# Copyright (c) 2020 Johan Kanflo (github.com/kanflo)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from typing import *
import json
import sys
import os
import calendar
from datetime import datetime, timedelta
import signal
import random
import time
import re
import errno
import sbs1
import utils
from collections import Counter


# Clean out observations this often
OBSERVATION_CLEAN_INTERVAL = 30

log = None

# http://stackoverflow.com/questions/1165352/fast-comparison-between-two-python-dictionary


class DictDiffer(object):
    """
    Calculate the difference between two dictionaries as:
    (1) items added
    (2) items removed
    (3) keys same in both but changed values
    (4) keys same in both and unchanged values
    """

    def __init__(self, current_dict, past_dict):
        self.current_dict, self.past_dict = current_dict, past_dict
        self.set_current, self.set_past = set(
            current_dict.keys()), set(past_dict.keys())
        self.intersect = self.set_current.intersection(self.set_past)

    def added(self):
        return self.set_current - self.intersect

    def removed(self):
        return self.set_past - self.intersect

    def changed(self):
        return set(o for o in self.intersect if self.past_dict[o] != self.current_dict[o])

    def unchanged(self):
        return set(o for o in self.intersect if self.past_dict[o] == self.current_dict[o])


class Observation(object):
    """
    This class keeps track of the observed flights around us.
    """
    __icao24 = None
    __squawk = None
    __flightID = None
    __loggedDate = None
    __callsign = None
    __altitude = None
    __altitudeTime = None
    __groundSpeed = None
    __track = None
    __lat = None
    __lon = None
    __latLonTime = None
    __verticalRate = None
    __operator = None
    __registration = None
    __type = None
    __updated = None
    __route = None
    __image_url = None
    __transmission_types = dict()

    def __repr__(self):
        return f" icao {self.__icao24} logged {self.__loggedDate} alt {self.__altitude} lat {self.__lat} lon {self.__lon} speed {self.__groundSpeed} track {self.__track}"

    def __init__(self, sbs1msg, now):
        log.debug("%s appeared" % sbs1msg["icao24"])
        self.__icao24 = sbs1msg["icao24"]
        self.__flightID = sbs1msg["flightID"]
        self.__squawk = sbs1msg["squawk"]
        self.__loggedDate = now  # sbs1msg["loggedDate"]
        self.__callsign = sbs1msg["callsign"]
        self.__altitude = sbs1msg["altitude"]
        self.__altitudeTime = now
        self.__groundSpeed = sbs1msg["groundSpeed"]  # mah not present
        self.__track = sbs1msg["track"]
        self.__lat = sbs1msg["lat"]
        self.__lon = sbs1msg["lon"]
        self.__latLonTime = now
        self.__verticalRate = sbs1msg["verticalRate"]
        self.__operator = None
        self.__registration = None
        self.__type = None
        self.__updated = True

    def update(self, sbs1msg, now):
        oldData = dict(self.__dict__)
        self.__loggedDate = now
        if sbs1msg["icao24"]:
            self.__icao24 = sbs1msg["icao24"]
        if sbs1msg["squawk"]:
            self.__squawk = sbs1msg["squawk"]
        if sbs1msg["flightID"]:
            self.__flightID = sbs1msg["flightID"]
        if sbs1msg["callsign"] and self.__callsign != sbs1msg["callsign"]:
            self.__callsign = sbs1msg["callsign"].rstrip()
        if sbs1msg["altitude"]:
            self.__altitude = sbs1msg["altitude"]
            self.__altitudeTime = now
        if sbs1msg["groundSpeed"]:
            self.__groundSpeed = sbs1msg["groundSpeed"]
        if sbs1msg["track"]:
            self.__track = sbs1msg["track"]
        if sbs1msg["lat"]:
            self.__lat = sbs1msg["lat"]
            self.__latLonTime = now
        if sbs1msg["lon"]:
            self.__lon = sbs1msg["lon"]
            self.__latLonTime = now
        if sbs1msg["verticalRate"]:
            self.__verticalRate = sbs1msg["verticalRate"]
        if not self.__verticalRate:
            self.__verticalRate = 0
        if sbs1msg["generatedDate"]:
            self.__generatedDate = sbs1msg["generatedDate"]
        # if sbs1msg["loggedDate"]:
        #    self.__loggedDate = sbs1msg["loggedDate"]

        # Check if observation was updated
        newData = dict(self.__dict__)
        del oldData["_Observation__loggedDate"]
        del newData["_Observation__loggedDate"]
        d = DictDiffer(oldData, newData)
        self.__updated = len(d.changed()) > 0

    def getIcao24(self) -> str:
        return self.__icao24

    def getcallsign(self) -> str:
        return self.__callsign

    def getsquawk(self) -> str:
        return self.__squawk

    def getflightID(self) -> str:
        return self.__flightID

    def getLat(self) -> float:
        return self.__lat

    def getLon(self) -> float:
        return self.__lon

    def isUpdated(self) -> bool:
        return self.__updated

    def getLoggedDate(self) -> datetime:
        return self.__loggedDate

    def getGroundSpeed(self) -> float:
        return round(self.__groundSpeed,1)

    def getHeading(self) -> float:
        return round(self.__track,1)

    def getAltitude(self) -> float:
        return self.__altitude

    def getVerticalRate(self) -> float:
        return self.__verticalRate

    def getType(self) -> str:
        return self.__type

    def getRegistration(self) -> str:
        return self.__registration

    def getOperator(self) -> str:
        return self.__operator

    def getRoute(self) -> str:
        return self.__route

    def getImageUrl(self) -> str:
        return self.__image_url

    def isPresentable(self) -> bool:
        if not (self.__altitude and self.__lat and self.__lon):
            return False
        # return self.__altitude and self.__groundSpeed and self.__track and self.__lat and self.__lon and self.__operator and self.__registration and self.__image_url
        return self.__altitude and self.__lat and self.__lon and self.__callsign and self.__groundSpeed and self.__track

    def as_dict(self) -> dict:
        d = {
            "icao24":  self.getIcao24(),
            "callsign":  self.getcallsign(),
            "squawk":  self.getsquawk(),
            "time":  time.time(),
            "lat":  self.getLat(),
            "lon":  self.getLon(),
            "altitude":  self.getAltitude(),
            "speed":  self.getGroundSpeed(),
            "vspeed":  self.getVerticalRate(),
            "heading":  self.getHeading()
        }
        return d


    def dict(self):
        d = dict(self.__dict__)
        if d["_Observation__verticalRate"] == None:
            d["verticalRate"] = 0
        if "_Observation__lastAlt" in d:
            del d["lastAlt"]
        if "_Observation__lastLat" in d:
            del d["lastLat"]
        if "_Observation__lastLon" in d:
            del d["lastLon"]
        d["loggedDate"] = "%s" % (d["_Observation__loggedDate"])
        return d


class FlightObserver(object):
    __observations: Dict[str, str] = {}
    __next_clean: datetime = None
    __msgTypes = {
        1: 'ES_IDENT_AND_CATEGORY',
        2: 'ES_SURFACE_POS',
        3: 'ES_AIRBORNE_POS',
        4: 'ES_AIRBORNE_VEL',
        5: 'SURVEILLANCE_ALT',
        6: 'SURVEILLANCE_ID',
        7: 'AIR_TO_AIR',
        8: 'ALL_CALL_REPLY'
        }
    __msgByType = Counter()
    __counters = Counter(messages=0, observations=0)

    def __init__(self):
        self.__observations = {}
        self.__next_clean = datetime.utcnow() + timedelta(seconds=OBSERVATION_CLEAN_INTERVAL)
        self.__message_rate = 0.
        self.__observation_rate = 0.

    def parse(self, data):
        now = datetime.utcnow()
        self.__counters['messages'] += 1
        self.cleanObservations(now)
        m = sbs1.parse(data)
        if m:
            self.__msgByType[m["transmissionType"]] += 1
            icao24 = m["icao24"]
            if icao24 in self.__observations:
                self.__observations[icao24].update(m, now)
            else:
                self.__observations[icao24] = Observation(m, now)

            if self.__observations[icao24].isPresentable():
                self.__counters['observations'] += 1
                return self.__observations[icao24].as_dict()
            return None


    def _distribution(self):
        s = sum(self.__msgByType.values())
        for k,v in self.__msgByType.most_common():
            p = round(v*100. / s,1)
            yield self.__msgTypes.get(k, 'UNKNOWN'), p

    def stats(self):
        r =  {
            'observations': len(self.__observations),
            'observation_rate': round(self.__observation_rate,1),
            'messagerate':  round(self.__message_rate,1)
        }
        return (r, self._distribution(), self.__observations, OBSERVATION_CLEAN_INTERVAL)

    def cleanObservations(self, now):
        """Clean observations for planes not seen in a while
        """
        if now > self.__next_clean:
            cleaned = []
            for icao24 in self.__observations:
                log.debug("[%s] %s -> %s : %s" % (icao24, self.__observations[icao24].getLoggedDate(
                ), self.__observations[icao24].getLoggedDate() + timedelta(seconds=OBSERVATION_CLEAN_INTERVAL), now))
                if self.__observations[icao24].getLoggedDate() + timedelta(seconds= OBSERVATION_CLEAN_INTERVAL) < now:
                    log.debug("%s disappeared" % (icao24))
                    cleaned.append(icao24)

            for icao24 in cleaned:
                del self.__observations[icao24]

            self.__next_clean = now + timedelta(seconds = OBSERVATION_CLEAN_INTERVAL)
            self.__message_rate = float(self.__counters['messages']) / OBSERVATION_CLEAN_INTERVAL
            self.__observation_rate = float(self.__counters['observations']) / OBSERVATION_CLEAN_INTERVAL
            self.__counters.clear()
